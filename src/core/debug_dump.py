#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug dump utilities for Alert Orchestrator.

Writes full calculation context for a specific asset (USDT) into
structured files (JSON + CSV) under the sandbox directory for
reproducible, offline analysis.
"""
from __future__ import annotations

import os
import json
import csv
import logging
from typing import List, Optional, Tuple
from datetime import datetime, timezone

import numpy as np

from ..shared.models import Transaction, AssetTimeSeries
from ..shared.config import ClientConfig
from ..shared.greptime_reader import GreptimeReader
from ..shared.correct_calculations import (
    calculate_correct_gains,
)

log = logging.getLogger(__name__)


def _to_iso_utc(dt: datetime) -> str:
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        # ISO8601 with Z suffix
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return str(dt)


def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def dump_asset_debug(
    *,
    asset_display: str,
    resolved_symbol: str,
    reader: GreptimeReader,
    cfg: ClientConfig,
    out_dir: str,
    alignment_method: str = "none",
    tx_timestamp_source: str = "timestamp",
) -> Optional[str]:
    """
    Dump full vectors used for corrected gains for a single asset using cfg.date_range.

    Writes two files:
    - JSON: metadata + arrays for exact reproducibility
    - CSV: denormalized per-timepoint table for quick spreadsheet analysis

    Returns the JSON path on success, or None when no data.
    """
    try:
        # Fetch positions by date_range
        series_obj: Optional[AssetTimeSeries] = reader.fetch_asset_series(resolved_symbol, cfg.date_range)
        if not series_obj or not series_obj.series:
            log.info(f"[debug_dump] No series for asset={asset_display} ({resolved_symbol}) in configured date range")
            return None

        # Fetch transactions by date_range
        txs: List[Transaction] = reader.fetch_transactions(
            asset_symbol=resolved_symbol,
            deposits_prefix=cfg.deposits_prefix,
            withdrawals_prefix=cfg.withdrawals_prefix,
            date_range=cfg.date_range,
        )

        # Prepare position vectors
        pos_ts = sorted(series_obj.series.keys())
        pos_vals = [float(series_obj.series[t]) for t in pos_ts]

        # Run the same calculation path the app uses
        timebase, interp_pos, dep_cdf, wdr_cdf, gains = calculate_correct_gains(
            position_timestamps=pos_ts,
            position_values=pos_vals,
            transactions=txs,
            reference_time_index=0,
            interpolation_method="linear",
            alignment_method=str(alignment_method or "none"),
            tx_timestamp_source=str(tx_timestamp_source or "timestamp"),
        )

        # Recover per-step vectors from CDFs
        dep_vec = np.diff(np.insert(dep_cdf, 0, 0.0)) if dep_cdf is not None else np.zeros_like(gains)
        wdr_vec = np.diff(np.insert(wdr_cdf, 0, 0.0)) if wdr_cdf is not None else np.zeros_like(gains)

        # Mark unified points that correspond to original position samples
        pos_ts_set = set(pos_ts)
        pos_mask = np.array([tb in pos_ts_set for tb in timebase], dtype=bool)

        # Build output directory
        os.makedirs(out_dir, exist_ok=True)
        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = f"usdt_debug_{ts_tag}"
        json_path = os.path.join(out_dir, base + ".json")
        csv_path = os.path.join(out_dir, base + ".csv")

        # JSON payload (metadata + arrays)
        payload = {
            "asset_display": asset_display,
            "resolved_symbol": resolved_symbol,
            "config_date_range": {
                "start": _to_iso_utc(cfg.date_range.start) if cfg.date_range and cfg.date_range.start else None,
                "end": _to_iso_utc(cfg.date_range.end) if cfg.date_range and cfg.date_range.end else None,
            },
            "alignment_method": str(alignment_method or "none"),
            "tx_timestamp_source": str(tx_timestamp_source or "timestamp"),
            "position_timestamps": [_to_iso_utc(t) for t in pos_ts],
            "position_values": [float(v) for v in pos_vals],
            "unified_timebase": [_to_iso_utc(t) for t in timebase],
            "interpolated_positions": [float(x) for x in interp_pos.tolist()],
            "deposit_vector": [float(x) for x in dep_vec.tolist()],
            "withdrawal_vector": [float(x) for x in wdr_vec.tolist()],
            "deposit_cdf": [float(x) for x in dep_cdf.tolist()],
            "withdrawal_cdf": [float(x) for x in wdr_cdf.tolist()],
            "gains": [float(x) for x in gains.tolist()],
            "pos_mask": [bool(b) for b in pos_mask.tolist()],
            "transactions": [
                {
                    "timestamp": _to_iso_utc(tx.timestamp),
                    "created_at": _to_iso_utc(tx.created_at) if getattr(tx, "created_at", None) else None,
                    "wallet_address": getattr(tx, "wallet_address", None),
                    "market_id": getattr(tx, "market_id", None),
                    "amount": float(getattr(tx, "amount", 0.0)),
                    "transaction_type": getattr(tx, "transaction_type", None),
                    "notes": getattr(tx, "notes", None),
                }
                for tx in txs
            ],
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        # CSV: denormalized per-timepoint view
        headers = [
            "idx",
            "ts",
            "is_pos_sample",
            "interp_position_usd",
            "deposit_step",
            "deposit_cdf",
            "withdrawal_step",
            "withdrawal_cdf",
            "gains_usd",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as fcsv:
            w = csv.writer(fcsv)
            w.writerow(headers)
            for i, tb in enumerate(timebase):
                w.writerow([
                    i,
                    _to_iso_utc(tb),
                    int(pos_mask[i]),
                    _safe_float(interp_pos[i]),
                    _safe_float(dep_vec[i] if i < len(dep_vec) else 0.0),
                    _safe_float(dep_cdf[i] if i < len(dep_cdf) else 0.0),
                    _safe_float(wdr_vec[i] if i < len(wdr_vec) else 0.0),
                    _safe_float(wdr_cdf[i] if i < len(wdr_cdf) else 0.0),
                    _safe_float(gains[i] if i < len(gains) else 0.0),
                ])

        log.info(f"[debug_dump] USDT debug written: json={json_path} csv={csv_path}")
        return json_path

    except Exception as e:
        log.warning(f"[debug_dump] Failed to dump asset debug for {asset_display}: {e}")
        return None
