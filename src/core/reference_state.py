#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reference state handling (Phase B scaffold)

Reads/writes reference state using withdrawals ledger tables. This file is a
Phase B scaffold and documents the intended interfaces without full logic.

Planned interfaces:
- get_last_reference(assets: list[str], keyword: str) -> dict[str, tuple[t0, V_i(t0)]]
  Reads the most recent withdrawal row per asset whose notes contains the
  keyword, returning the reference timestamp and USD value at that time.

- preview_reference_write(asset: str, keyword: str, V_i_t1: float, ts: datetime) -> str
  Returns the SQL (or summary) that would be executed to write a reference row
  to the test table, for dry-run preview.

- write_reference(asset: str, keyword: str, V_i_t1: float, ts: datetime, test_prefix: bool) -> bool
  Executes the write (to test tables when test_prefix is True). Follows the
  client's write safety model.

Note: Logic will rely on shared GreptimeReader/GreptimeWriter and use
`client.withdrawals_prefix` for target tables.
"""

from __future__ import annotations

from typing import Dict, Tuple, Optional
from datetime import datetime, timezone

from ..shared.config import ClientConfig, DateRange
from ..shared.greptime_reader import GreptimeReader


def _nearest_value(series: dict[datetime, float], target_ts: datetime) -> Optional[float]:
    if not series:
        return None
    # Find timestamp with minimal absolute delta
    nearest_ts = min(series.keys(), key=lambda ts: abs((ts - target_ts).total_seconds()))
    return float(series.get(nearest_ts, 0.0))


def get_last_reference(
    reader: GreptimeReader,
    cfg: ClientConfig,
    assets: list[str],
    keyword: str,
) -> Dict[str, Tuple[datetime, float]]:
    """Return last reference per asset as (t0, V_i(t0)).

    Finds the latest withdrawal whose notes contain `keyword` and computes the
    portfolio USD value at that timestamp from the positions series.
    """
    results: Dict[str, Tuple[datetime, float]] = {}
    dr: Optional[DateRange] = cfg.date_range

    for asset in assets:
        # 1) Read transactions and find last tagged withdrawal
        try:
            txs = reader.fetch_transactions(
                asset_symbol=asset,
                deposits_prefix=cfg.deposits_prefix,
                withdrawals_prefix=cfg.withdrawals_prefix,
                date_range=dr,
            )
        except Exception:
            continue

        tagged = [t for t in txs if t.transaction_type == "withdrawal" and t.notes and keyword in t.notes]
        if not tagged:
            continue
        last = tagged[-1]
        t0 = last.timestamp.astimezone(timezone.utc)

        # 2) Read positions series and derive V_i(t0)
        try:
            series_obj = reader.fetch_asset_series(asset, dr)
        except Exception:
            continue
        if not series_obj or not series_obj.series:
            continue
        v0 = _nearest_value(series_obj.series, t0)
        if v0 is None:
            continue
        results[asset] = (t0, float(v0))

    return results


def preview_reference_write(asset: str, keyword: str, V_i_t1: float, ts: DatetimeLike) -> str:
    """Stub: returns a SQL preview for the reference write.
    """
    raise NotImplementedError


def write_reference(asset: str, keyword: str, V_i_t1: float, ts: DatetimeLike, test_prefix: bool = True) -> bool:
    """Stub: executes the write to GreptimeDB (test tables by default).
    """
    raise NotImplementedError

