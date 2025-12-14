#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GreptimeDB Writer for Liqwid Client (Phase 7: execution wired)

Implements a minimal, safe writer using the same HTTP style as GreptimeReader.
Respects test_prefix for database/table scoping. Provides helpers to build
DDL/INSERT SQL and to execute batched writes grouped by asset symbol.

See instructions in:
- client/instructions/step-by-step-depositwithdrawals.md
- client/instructions/depositwithdrawals.md
"""

import logging
import json
from urllib.parse import urljoin
import requests
from datetime import datetime
from typing import Dict, List, Literal
from .utils import datetime_to_timestamp, normalize_asset_symbol

from .config import GreptimeConnConfig
from .models import Transaction


class GreptimeWriter:
    """
    Scaffold for a minimal, safe writer to GreptimeDB.

    Constructor arguments mirror the existing reader configuration style and
    add table prefix controls for deposits and withdrawals. A test_prefix flag
    is included to support writing to test_ database/table scopes.

    All methods are stubs for now and raise NotImplementedError.
    """

    def __init__(
        self,
        config: GreptimeConnConfig,
        deposits_prefix: str = "liqwid_deposits_",
        withdrawals_prefix: str = "liqwid_withdrawals_",
        test_prefix: bool = False,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config = config
        self.deposits_prefix = deposits_prefix
        self.withdrawals_prefix = withdrawals_prefix
        self.test_prefix = test_prefix

        # Build URLs and HTTP session (mirror greptime_reader)
        self.base_url = f"{config.host}:{config.port}"
        self.sql_endpoint = urljoin(self.base_url, "/v1/sql")
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'LiqwidClientWriter/1.0'
        })

        self.logger.info(
            "Initialized GreptimeWriter with db=%s, test_prefix=%s",
            self.config.database,
            self.test_prefix,
        )

    # ---------- Internal helpers (stubs) ----------
    def _execute_sql(self, sql: str, use_effective_db: bool = True) -> Dict:
        """
        Execute SQL against GreptimeDB using HTTP POST to /v1/sql.
        Uses effective database when test_prefix is enabled.
        """
        try:
            data = {'sql': sql}
            eff_db = self.get_effective_database()
            if use_effective_db and eff_db:
                data['db'] = eff_db
            self.logger.debug("Executing SQL: %s", sql.splitlines()[0][:120])
            resp = self.session.post(self.sql_endpoint, data=data, timeout=self.config.timeout)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
            try:
                result = resp.json()
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON response: {e}")
            # Greptime success usually has 'output' or code==0
            if isinstance(result, dict) and (result.get('code') == 0 or 'output' in result):
                return result
            return result
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Request error: {e}")

    def _format_timestamp(self, dt: datetime) -> int:
        """
        Convert a timezone-aware datetime to milliseconds since epoch (UTC).
        """
        if not isinstance(dt, datetime):
            raise TypeError("dt must be a datetime")
        return int(datetime_to_timestamp(dt))

    def _get_transaction_table_name(self, asset_symbol: str, tx_type: Literal["deposit", "withdrawal"]) -> str:
        """
        Compute the per-asset transaction table name, applying prefixes and test scope.
        """
        symbol = normalize_asset_symbol(asset_symbol)
        if tx_type not in ("deposit", "withdrawal"):
            raise ValueError("tx_type must be 'deposit' or 'withdrawal'")
        base = (self.deposits_prefix if tx_type == "deposit" else self.withdrawals_prefix) + symbol
        return f"test_{base}" if self.test_prefix else base

    def ensure_transaction_table(self, asset_symbol: str, tx_type: Literal["deposit", "withdrawal"]) -> None:
        """
        Create the transaction table if it does not exist, with the required schema.
        """
        table = self._get_transaction_table_name(asset_symbol, tx_type)
        ddl = self._build_create_table_sql(table)
        self._execute_sql(ddl)

    def ensure_database_exists(self) -> None:
        """
        Ensure the effective database exists (used for --test-prefix writes).
        Uses CREATE DATABASE IF NOT EXISTS <db>.
        """
        eff_db = self.get_effective_database()
        if not eff_db:
            return
        # Use a raw call without binding the 'db' param, since the DB may not exist yet.
        self._execute_sql(f"CREATE DATABASE IF NOT EXISTS {eff_db}", use_effective_db=False)

    def get_effective_database(self) -> str:
        """
        Compute the effective database name applying test_ prefix if enabled.
        """
        base_db = self.config.database or ""
        return f"test_{base_db}" if self.test_prefix and base_db else base_db

    def count_transactions(self, asset_symbol: str, tx_type: Literal["deposit", "withdrawal"]) -> int:
        """
        Return COUNT(*) from the target transaction table, or -1 if unavailable.
        """
        try:
            table = self._get_transaction_table_name(asset_symbol, tx_type)
            result = self._execute_sql(f"SELECT COUNT(*) as cnt FROM {table}")
            # Minimal parse: output[0].records.rows[0][0]
            output = result.get('output') if isinstance(result, dict) else None
            if not output:
                return -1
            for block in output:
                recs = block.get('records') if isinstance(block, dict) else None
                if not recs:
                    continue
                rows = recs.get('rows') if isinstance(recs, dict) else None
                if rows and len(rows) > 0 and isinstance(rows[0], list) and len(rows[0]) > 0:
                    try:
                        return int(rows[0][0])
                    except Exception:
                        return -1
            return -1
        except Exception:
            return -1

    def _build_transaction_insert_sql(self, table: str, txs: List[Transaction]) -> str:
        """
        Build a bulk INSERT SQL statement for the provided transactions.
        Matches production column set/order: ts, created_at, wallet_address, market_id, amount, notes
        (no asset_symbol stored; table naming uses asset_symbol but it is not persisted).
        """
        if not txs:
            return ""
        # Helper to escape single quotes in strings
        def esc(s: str) -> str:
            if s is None:
                return "NULL"
            return "'" + str(s).replace("'", "''") + "'"

        values_rows: List[str] = []
        for t in txs:
            ts_ms = self._format_timestamp(t.timestamp)
            created_ms = self._format_timestamp(t.created_at)
            row = (
                str(ts_ms),            # ts
                str(created_ms),       # created_at
                esc(t.wallet_address), # wallet_address
                esc(t.market_id),      # market_id
                str(float(t.amount)),  # amount
                esc(t.notes),          # notes
            )
            values_rows.append(f"({', '.join(row)})")

        sql = (
            f"INSERT INTO {table} (\n"
            f"    ts, created_at, wallet_address, market_id, amount, notes\n"
            f") VALUES {', '.join(values_rows)}"
        )
        return sql

    # Internal helper for dry-run DDL preview
    def _build_create_table_sql(self, table: str) -> str:
        """
        Build the CREATE TABLE IF NOT EXISTS statement for transactions schema.
        Matches production column set/order: ts, created_at, wallet_address, market_id, amount, notes
        """
        return (
            f"CREATE TABLE IF NOT EXISTS {table} (\n"
            f"    ts TIMESTAMP TIME INDEX,\n"
            f"    created_at TIMESTAMP,\n"
            f"    wallet_address STRING,\n"
            f"    market_id STRING,\n"
            f"    amount DOUBLE,\n"
            f"    notes STRING\n"
            f");"
        )

    # ---------- Public API (stubs) ----------
    def insert_transactions(self, txs: List[Transaction], tx_type: Literal["deposit", "withdrawal"]) -> Dict[str, int]:
        """
        Insert a batch of transactions, grouped by asset_symbol.
        Returns a mapping of table_name -> rows_inserted (best-effort).
        """
        if not txs:
            return {}
        # Group by asset_symbol
        groups: Dict[str, List[Transaction]] = {}
        for t in txs:
            key = t.asset_symbol.lower()
            groups.setdefault(key, []).append(t)

        results: Dict[str, int] = {}
        # Ensure database exists prior to creating tables or inserting
        self.ensure_database_exists()
        for asset, group in groups.items():
            table = self._get_transaction_table_name(asset, tx_type)
            # Ensure table exists
            self.ensure_transaction_table(asset, tx_type)
            # Build SQL and execute
            sql = self._build_transaction_insert_sql(table, group)
            if not sql.strip():
                results[table] = 0
                continue
            self._execute_sql(sql)
            results[table] = len(group)
        return results

    def record_deposit(self, tx: Transaction) -> bool:
        """
        Convenience wrapper for inserting a single deposit transaction.
        """
        res = self.insert_transactions([tx], "deposit")
        return sum(res.values()) == 1

    def record_withdrawal(self, tx: Transaction) -> bool:
        """
        Convenience wrapper for inserting a single withdrawal transaction.
        """
        res = self.insert_transactions([tx], "withdrawal")
        return sum(res.values()) == 1
