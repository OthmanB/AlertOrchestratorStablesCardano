#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IO adapters for orchestrator: centralize reads and source mapping.

Positions and transactions always come from the liqwid Greptime database.
Price series come from the selected source (greptime(liqwid) or greptime(minswap)).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Literal, Tuple
from datetime import datetime

from ..shared.config import ClientConfig, DateRange, GreptimeConnConfig
from ..shared.greptime_reader import GreptimeReader
from ..shared.models import AssetTimeSeries, Transaction

DataSourceName = Literal["greptime(liqwid)", "greptime(minswap)"]


def _reader_for_db(base: GreptimeConnConfig, dbname: str, table_prefix: str) -> GreptimeReader:
	import copy
	gcfg = copy.deepcopy(base)
	gcfg.database = dbname
	return GreptimeReader(gcfg, table_prefix)


def get_positions(asset_symbol: str, cfg: ClientConfig, date_range: Optional[DateRange]) -> Optional[AssetTimeSeries]:
	"""Fetch aggregate positions from liqwid DB regardless of source."""
	reader = _reader_for_db(cfg.greptime, "liqwid", cfg.table_asset_prefix)
	return reader.fetch_asset_series(asset_symbol, date_range)


def get_positions_by_wallet(asset_symbol: str, cfg: ClientConfig, date_range: Optional[DateRange]) -> Dict[str, AssetTimeSeries]:
	reader = _reader_for_db(cfg.greptime, "liqwid", cfg.table_asset_prefix)
	return reader.fetch_asset_series_by_wallet(asset_symbol, date_range)


def get_transactions(asset_symbol: str, cfg: ClientConfig, date_range: Optional[DateRange], wallet_address: Optional[str] = None) -> List[Transaction]:
	reader = _reader_for_db(cfg.greptime, "liqwid", cfg.table_asset_prefix)
	return reader.fetch_transactions(
		asset_symbol=asset_symbol,
		deposits_prefix=cfg.deposits_prefix,
		withdrawals_prefix=cfg.withdrawals_prefix,
		date_range=date_range,
		wallet_address=wallet_address,
	)


def get_price_series(asset_symbol: str, source: DataSourceName, cfg: ClientConfig, date_range: Optional[DateRange]) -> Optional[AssetTimeSeries]:
	"""
	Fetch USD price series for an asset from Greptime DB inferred by source name.

	Expected table naming convention in each DB (minswap|liqwid):
	  prices_<symbol> or <db>_prices_<symbol>
	We use a dedicated prefix mapping; fall back to detection.
	"""
	import logging
	log = logging.getLogger(__name__)
	s = str(source).strip().lower()
	log.info(f"[RATE_DEBUG] get_price_series called: asset={asset_symbol}, source={source}, table_prefix={cfg.table_asset_prefix}")
	if not (s.startswith("greptime(") and s.endswith(")")):
		log.warning(f"[RATE_DEBUG] Invalid source format: {source}")
		return None
	dbname = s[len("greptime("):-1]
	log.info(f"[RATE_DEBUG] Extracted dbname: {dbname}")
	# Build reader for selected DB. For liqwid we read price_usd from supply tables.
	if dbname == "minswap":
		price_prefix = "minswap_prices_"
		log.info(f"[RATE_DEBUG] Using minswap with prefix: {price_prefix}")
		reader = _reader_for_db(cfg.greptime, dbname, price_prefix)
		result = reader.fetch_price_series(asset_symbol, date_range)
		log.info(f"[RATE_DEBUG] minswap fetch_price_series returned: {result is not None}, points={len(result.series) if result and result.series else 0}")
		return result
	else:
		# Use the standard supply positions prefix from config, but target liqwid DB
		# Prices are unique per timestamp in liqwid supply tables, so a simple SELECT ts, price_usd is sufficient.
		log.info(f"[RATE_DEBUG] Using liqwid with prefix: {cfg.table_asset_prefix}, db=liqwid")
		reader = _reader_for_db(cfg.greptime, "liqwid", cfg.table_asset_prefix)
		result = reader.fetch_price_series(asset_symbol, date_range)
		log.info(f"[RATE_DEBUG] liqwid fetch_price_series returned: {result is not None}, points={len(result.series) if result and result.series else 0}")
		return result


def compute_duty_cycle(series: Optional[AssetTimeSeries], date_range: Optional[DateRange]) -> float:
	"""
	Duty cycle: fraction of timestamps available within requested range.
	We approximate using record count vs expected samples if range provided; if not, use 1.0 if non-empty else 0.0.
	"""
	if series is None or not series.series:
		return 0.0
	if date_range is None or date_range.start is None or date_range.end is None:
		return 1.0
	# Estimate expected samples by median delta
	ts = sorted(series.series.keys())
	if len(ts) < 2:
		return 0.0
	import numpy as np
	deltas = np.diff([t.timestamp() for t in ts])
	median_dt = float(np.median(deltas)) if len(deltas) else 0.0
	if median_dt <= 0:
		return 0.0
	total_sec = (date_range.end - date_range.start).total_seconds()
	expected = max(1.0, total_sec / median_dt)
	observed = float(len(ts))
	return max(0.0, min(1.0, observed / expected))


def get_change_rate_series_usd(asset_symbol: str, usd_source: DataSourceName, cfg: ClientConfig, date_range: Optional[DateRange]) -> Optional[AssetTimeSeries]:
	"""
	Build the USD change-rate series r_usd(t) = price_usd(t) using the selected global USD source.
	This simply forwards to get_price_series for the chosen source.
	"""
	return get_price_series(asset_symbol, usd_source, cfg, date_range)


def get_change_rate_series_ada(asset_symbol: str, cfg: ClientConfig, date_range: Optional[DateRange]) -> Optional[AssetTimeSeries]:
	"""
	Build the ADA change-rate series r_ada(t) = price_usd(t) / ada_usd(t) using Minswap tables.
	- Reads from greptime(minswap) minswap_prices_<symbol> with columns ts, price_usd, ada_usd.
	- Returns AssetTimeSeries with series mapping ts -> r_ada.
	"""
	# Use minswap DB and price tables prefix
	reader = _reader_for_db(cfg.greptime, "minswap", "minswap_prices_")
	dual = reader.fetch_dual_price_series(asset_symbol, date_range)
	if not dual:
		return None
	price_usd_series, ada_usd_series = dual
	if not price_usd_series.series or not ada_usd_series.series:
		return None
	# Intersection on timestamps
	ts_usd = set(price_usd_series.series.keys())
	ts_ada = set(ada_usd_series.series.keys())
	ts_int = sorted(ts_usd & ts_ada)
	if not ts_int:
		return None
	series_data: dict[datetime, float] = {}
	for ts in ts_int:
		try:
			p = float(price_usd_series.series[ts])
			a = float(ada_usd_series.series[ts])
			if a and a != 0.0:
				series_data[ts] = p / a
		except Exception:
			continue
	return AssetTimeSeries(asset_symbol=asset_symbol.upper(), series=series_data)

