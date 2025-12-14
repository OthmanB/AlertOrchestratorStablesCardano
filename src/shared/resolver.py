#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hybrid Asset Resolver (scaffold) for Alert Orchestrator

Copied and adapted from client/resolver.py to avoid cross-package imports.
Resolves a user-provided asset identifier into a canonical pair
(market_id, asset_symbol) using a Greptime-first strategy.
"""
from __future__ import annotations

from typing import Optional, Tuple, Dict, Any, List
import logging


class Resolver:
    def __init__(
        self,
        greptime_reader: Optional[Any] = None,
        liqwid_client: Optional[Any] = None,
        cache: Optional[Dict[str, Tuple[str, str]]] = None,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.greptime_reader = greptime_reader
        self.liqwid_client = liqwid_client
        self.cache = cache if cache is not None else {}
        self.logger.info("Initialized Resolver — Greptime-first strategy enabled; Liqwid fallback when provided")

    def resolve_asset(self, input_str: str) -> Tuple[str, str]:
        if not input_str or not str(input_str).strip():
            raise ValueError("input_str cannot be empty")

        key = str(input_str).strip()
        key_lower = key.lower()

        # Cache lookup
        if key_lower in self.cache:
            return self.cache[key_lower]

        # Helper: safe parse using GreptimeReader's parser if available
        def parse_result(result: Dict[str, Any], expected: List[str]) -> List[Dict[str, Any]]:
            try:
                if self.greptime_reader and hasattr(self.greptime_reader, "_parse_query_response"):
                    return self.greptime_reader._parse_query_response(result, expected)  # type: ignore[attr-defined]
            except Exception:
                pass

            # Fallback minimal parser
            records_out: List[Dict[str, Any]] = []
            try:
                outputs = result.get("output", []) if isinstance(result, dict) else []
                for block in outputs:
                    recs = block.get("records", {}) if isinstance(block, dict) else {}
                    schema = recs.get("schema", {})
                    cols = schema.get("column_schemas", [])
                    name_to_idx: Dict[str, int] = {}
                    for i, col in enumerate(cols):
                        nm = col.get("name", f"col_{i}")
                        name_to_idx[nm] = i
                    rows = recs.get("rows", [])
                    for row in rows:
                        rec: Dict[str, Any] = {}
                        for col in expected:
                            idx = name_to_idx.get(col)
                            rec[col] = row[idx] if idx is not None and idx < len(row) else None
                        records_out.append(rec)
            except Exception:
                return []
            return records_out

        # 1) Greptime-first strategy
        if self.greptime_reader and hasattr(self.greptime_reader, "_execute_sql"):
            # 1a) Check unified table presence via SHOW TABLES first
            unified_exists = False
            unified_table_name = "liqwid_supply_positions"
            try:
                if hasattr(self.greptime_reader, "_show_tables"):
                    table_names = self.greptime_reader._show_tables()  # type: ignore[attr-defined]
                    for t in table_names:
                        base = str(t).split(".")[-1].strip().lower()
                        if base == "liqwid_supply_positions":
                            unified_exists = True
                            unified_table_name = str(t)
                            break
                else:
                    show = self.greptime_reader._execute_sql("SHOW TABLES")  # type: ignore[attr-defined]
                    tables = set()
                    for rec in parse_result(show, ["Tables"]):
                        t = rec.get("Tables")
                        if isinstance(t, str):
                            tables.add(t)
                    if "liqwid_supply_positions" in tables:
                        unified_exists = True
            except Exception:
                unified_exists = False

            # 1b) Try unified mapping only if present
            if unified_exists:
                try:
                    result = self.greptime_reader._execute_sql(
                        f"SELECT DISTINCT market_id, market_name, asset_symbol FROM {unified_table_name} LIMIT 500"
                    )  # type: ignore[attr-defined]
                    rows = parse_result(result, ["market_id", "market_name", "asset_symbol"]) or []
                    for rec in rows:
                        market_id = str(rec.get("market_id") or "").strip()
                        market_name = str(rec.get("market_name") or "").strip()
                        asset_symbol = str(rec.get("asset_symbol") or "").strip().lower()
                        if not market_id and not market_name:
                            continue
                        if key_lower in {market_id.lower(), market_name.lower(), asset_symbol.lower()}:
                            self.cache[key_lower] = (market_id or market_name, asset_symbol)
                            return self.cache[key_lower]
                except Exception:
                    # If unified query fails, fall through to per-asset discovery
                    pass

            # 1c) Iterate per-asset tables discovered via SHOW TABLES
            try:
                # Discover asset symbols
                assets = []
                if hasattr(self.greptime_reader, "discover_asset_tables"):
                    assets = self.greptime_reader.discover_asset_tables()  # type: ignore[attr-defined]
                # If discovery failed/empty, no assumptions; stop here for Greptime path
                for symbol in assets:
                    table = self.greptime_reader._get_table_name(symbol)  # type: ignore[attr-defined]
                    try:
                        # Prefer both market_id and market_name if available
                        result = self.greptime_reader._execute_sql(
                            f"SELECT market_id, market_name FROM {table} LIMIT 1"
                        )  # type: ignore[attr-defined]
                        rows = parse_result(result, ["market_id", "market_name"]) or []
                        if not rows:
                            # Try only market_id if market_name missing
                            result = self.greptime_reader._execute_sql(
                                f"SELECT market_id FROM {table} LIMIT 1"
                            )  # type: ignore[attr-defined]
                            rows = parse_result(result, ["market_id"]) or []
                        if rows:
                            rec = rows[0]
                            market_id = str(rec.get("market_id") or "").strip()
                            market_name = str(rec.get("market_name") or "").strip()
                            if key_lower in {symbol.lower(), market_id.lower(), market_name.lower()}:
                                self.cache[key_lower] = (market_id or market_name or symbol.upper(), symbol.lower())
                                return self.cache[key_lower]
                    except Exception:
                        continue
            except Exception:
                pass

        # 2) Liqwid fallback (no-op here unless a client is provided)
        try:
            if self.liqwid_client and hasattr(self.liqwid_client, "fetch_markets"):
                markets = self.liqwid_client.fetch_markets()
                for m in markets:
                    try:
                        market_id = getattr(m, "id", "") or getattr(m, "symbol", "") or ""
                        display_name = getattr(m, "name", "") or getattr(m, "displayName", "") or ""
                        asset_symbol = getattr(m, "underlying_symbol", "") or getattr(getattr(m, "asset", None), "symbol", "") or ""
                        if key_lower in {str(market_id).lower(), str(display_name).lower(), str(asset_symbol).lower()}:
                            asset_symbol_norm = str(asset_symbol).strip().lower()
                            if not asset_symbol_norm:
                                continue
                            self.cache[key_lower] = (str(market_id), asset_symbol_norm)
                            return self.cache[key_lower]
                    except Exception:
                        continue
        except Exception:
            pass

        raise RuntimeError(f"Unable to resolve asset identifier '{input_str}' via Greptime or Liqwid; no changes made.")
