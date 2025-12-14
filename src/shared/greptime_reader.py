#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GreptimeDB Reader for Liqwid Client Aggregator
Handles database queries, response parsing, and time series data extraction.
"""

import json
import logging
import requests
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urljoin, urlparse

from .config import GreptimeConnConfig, DateRange
from .models import AssetTimeSeries, Transaction
from .utils import (
    timestamp_to_datetime,
    datetime_to_timestamp,
    build_date_range_filter,
    validate_table_name,
    retry_with_backoff,
    safe_float,
    normalize_asset_symbol,
    ProgressTracker
)


class GreptimeError(Exception):
    """Base exception for GreptimeDB operations"""
    pass


class GreptimeConnectionError(GreptimeError):
    """Connection-related errors"""
    pass


class GreptimeQueryError(GreptimeError):
    """Query execution errors"""
    pass


class GreptimeReader:
    """
    GreptimeDB HTTP client for reading Liqwid supply position time series data
    
    Handles:
    - SQL query execution via HTTP
    - Multi-table asset data retrieval
    - Response parsing and normalization
    - Error handling and retries
    """
    
    def __init__(self, config: GreptimeConnConfig, table_prefix: str = "liqwid_supply_positions_"):
        """
        Initialize GreptimeDB reader
        
        Args:
            config: GreptimeDB connection configuration
            table_prefix: Prefix for asset tables
        """
        self.config = config
        self.table_prefix = table_prefix
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Build URLs robustly
        raw_host = (config.host or "").strip()
        parsed = urlparse(raw_host)
        scheme = parsed.scheme or "http"
        # If netloc is empty (e.g., host provided without scheme), parsed.path holds the host
        netloc = parsed.netloc or parsed.path
        # Strip any accidental path segment and keep only host[:port]
        if "/" in netloc:
            netloc = netloc.split("/")[0]
        # Append port if not already present
        if ":" not in netloc and config.port:
            netloc = f"{netloc}:{config.port}"
        self.base_url = f"{scheme}://{netloc}"
        self.sql_endpoint = f"{self.base_url}/v1/sql"
        
        # Session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'LiqwidClientAggregator/1.0'
        })
        
        self.logger.info(f"Initialized GreptimeDB reader: {self.base_url}")
    
    def _execute_sql(self, sql: str) -> Dict[str, Any]:
        """
        Execute SQL query against GreptimeDB
        
        Args:
            sql: SQL query string
            
        Returns:
            Response data from GreptimeDB
            
        Raises:
            GreptimeConnectionError: Connection issues
            GreptimeQueryError: Query execution issues
        """
        def _do_request():
            self.logger.debug(f"Executing SQL: {sql[:100]}...")
            
            # Prepare request data
            data = {'sql': sql}
            if self.config.database:
                data['db'] = self.config.database
            
            # Execute request
            response = self.session.post(
                self.sql_endpoint,
                data=data,
                timeout=self.config.timeout
            )
            
            # Check HTTP status
            if response.status_code != 200:
                raise GreptimeQueryError(f"HTTP {response.status_code}: {response.text}")
            
            # Parse JSON response
            try:
                result = response.json()
                self.logger.debug(f"SQL execution successful")
                return result
            except json.JSONDecodeError as e:
                raise GreptimeQueryError(f"Invalid JSON response: {e}")
        
        # Execute with retry logic
        try:
            result = retry_with_backoff(
                _do_request,
                max_attempts=3,
                base_delay=1.0,
                exceptions=(requests.exceptions.RequestException, GreptimeQueryError)
            )
            if not isinstance(result, dict):
                raise GreptimeQueryError("Invalid response format")
            return result
        except requests.exceptions.ConnectionError as e:
            raise GreptimeConnectionError(f"Failed to connect to GreptimeDB: {e}")
        except requests.exceptions.Timeout as e:
            raise GreptimeConnectionError(f"Request timeout: {e}")
    
    def test_connection(self) -> bool:
        """
        Test connection to GreptimeDB
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            result = self._execute_sql("SELECT 1 as test")
            # GreptimeDB returns either {'code': 0} or {'output': [...]} on success
            return 'output' in result or result.get('code') == 0
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False
    
    def _get_table_name(self, asset_symbol: str) -> str:
        """
        Generate table name for an asset
        
        Args:
            asset_symbol: Asset symbol (e.g., 'USDC', 'DJED')
            
        Returns:
            Full table name (lowercase for GreptimeDB compatibility)
        """
        normalized_symbol = normalize_asset_symbol(asset_symbol)
        return f"{self.table_prefix}{normalized_symbol}"
    
    def _parse_query_response(self, result: Dict[str, Any], expected_columns: List[str]) -> List[Dict[str, Any]]:
        """
        Parse GreptimeDB query response into list of records
        
        Args:
            result: Raw response from GreptimeDB
            expected_columns: Expected column names in order
            
        Returns:
            List of records as dictionaries
            
        Raises:
            GreptimeQueryError: If response format is invalid
        """
        try:
            # Check for error response
            if 'code' in result and result['code'] != 0:
                raise GreptimeQueryError(f"Query failed: {result}")
            
            # Handle empty results
            if 'output' not in result or not result['output']:
                self.logger.debug("Query returned no results")
                return []
            
            records = []
            
            for output_block in result['output']:
                if 'records' not in output_block:
                    continue
                
                query_records = output_block['records']
                if not query_records:
                    continue
                
                # Get schema (field names)
                schema = query_records.get('schema', {})
                column_schemas = schema.get('column_schemas', [])
                
                # Map column names to indices
                column_map = {}
                for i, col_schema in enumerate(column_schemas):
                    column_name = col_schema.get('name', f'col_{i}')
                    column_map[column_name] = i
                
                # Process rows
                rows = query_records.get('rows', [])
                for row in rows:
                    record = {}
                    for col_name in expected_columns:
                        col_index = column_map.get(col_name)
                        if col_index is not None and col_index < len(row):
                            record[col_name] = row[col_index]
                        else:
                            record[col_name] = None
                    records.append(record)
            
            self.logger.debug(f"Parsed {len(records)} records from response")
            return records
            
        except Exception as e:
            raise GreptimeQueryError(f"Failed to parse query response: {e}")
    
    def _table_exists(self, table_name: str) -> bool:
        """
        Check if a table exists
        
        Args:
            table_name: Table name to check
            
        Returns:
            True if table exists
        """
        # Use a direct request (no backoff) so missing tables return quickly
        sql = f"DESCRIBE TABLE {table_name}"
        data = {'sql': sql}
        if self.config.database:
            data['db'] = self.config.database
        try:
            resp = self.session.post(self.sql_endpoint, data=data, timeout=self.config.timeout)
        except requests.exceptions.RequestException as e:
            self.logger.debug(f"_table_exists request error for {table_name}: {e}")
            return False

        if resp.status_code == 200:
            try:
                result = resp.json()
            except json.JSONDecodeError:
                return False
            return bool(result.get('output'))

        # Non-200: check if it's a standard 'table not found' error
        try:
            payload = resp.json()
            msg = str(payload)
        except Exception:
            msg = resp.text or ""
        if 'Table not found' in msg:
            return False
        # For other errors, be conservative and return False, but log
        self.logger.debug(f"_table_exists unexpected response {resp.status_code} for {table_name}: {msg}")
        return False
    
    def fetch_asset_series(
        self,
        asset_symbol: str,
        date_range: Optional[DateRange] = None
    ) -> Optional[AssetTimeSeries]:
        """
        Fetch time series data for a single asset
        
        Args:
            asset_symbol: Asset symbol to fetch
            date_range: Optional date range filter
            
        Returns:
            AssetTimeSeries or None if table doesn't exist
            
        Raises:
            GreptimeError: If query fails
        """
        table_name = self._get_table_name(asset_symbol)
        
        # Validate table name for security
        if not validate_table_name(table_name, self.table_prefix):
            raise GreptimeQueryError(f"Invalid table name: {table_name}")
        
        # Check if table exists
        if not self._table_exists(table_name):
            self.logger.warning(f"Table not found for asset={asset_symbol}, skipping")
            return None
        
        self.logger.info(f"Fetching series for asset={asset_symbol.upper()}")
        
        try:
            # Build date range filter
            date_filter = ""
            if date_range:
                date_filter = build_date_range_filter(date_range.start, date_range.end)
            
            # Build SQL query - aggregate USD values across wallets by timestamp
            sql = f"""
            SELECT ts, SUM(COALESCE(usd_value, underlying_units * price_usd, 0)) AS usd_value_sum
            FROM {table_name}
            {date_filter}
            GROUP BY ts
            ORDER BY ts ASC
            """
            
            # Execute query
            result = self._execute_sql(sql)
            
            # Parse results
            records = self._parse_query_response(result, ['ts', 'usd_value_sum'])
            
            # Convert to time series
            series_data = {}
            for record in records:
                timestamp_ms = record.get('ts')
                usd_value = record.get('usd_value_sum')
                
                if timestamp_ms is not None and usd_value is not None:
                    dt = timestamp_to_datetime(int(timestamp_ms))
                    series_data[dt] = safe_float(usd_value)
            
            self.logger.info(f"Retrieved {len(series_data)} data points for {asset_symbol.upper()}")
            
            return AssetTimeSeries(
                asset_symbol=asset_symbol.upper(),
                series=series_data
            )
            
        except Exception as e:
            error_msg = f"Failed to fetch series for {asset_symbol}: {e}"
            self.logger.error(error_msg)
            raise GreptimeError(error_msg)

    def fetch_asset_units_series(
        self,
        asset_symbol: str,
        date_range: Optional[DateRange] = None
    ) -> Optional[AssetTimeSeries]:
        """
        Fetch per-timestamp total underlying units for a single asset.

        Returns AssetTimeSeries with series mapping ts -> total_units (float).
        """
        table_name = self._get_table_name(asset_symbol)
        from .utils import validate_table_name, build_date_range_filter, timestamp_to_datetime, safe_float
        # Validate and existence
        if not validate_table_name(table_name, self.table_prefix):
            raise GreptimeQueryError(f"Invalid table name: {table_name}")
        if not self._table_exists(table_name):
            self.logger.warning(f"Table not found for asset={asset_symbol}, skipping units fetch")
            return None
        try:
            date_filter = ""
            if date_range:
                date_filter = build_date_range_filter(date_range.start, date_range.end)
            sql = f"""
            SELECT ts, SUM(COALESCE(underlying_units, 0)) AS units_sum
            FROM {table_name}
            {date_filter}
            GROUP BY ts
            ORDER BY ts ASC
            """
            result = self._execute_sql(sql)
            records = self._parse_query_response(result, ['ts', 'units_sum'])
            series_data = {}
            for record in records:
                ts_ms = record.get('ts')
                units = record.get('units_sum')
                if ts_ms is None or units is None:
                    continue
                dt = timestamp_to_datetime(int(ts_ms))
                series_data[dt] = safe_float(units)
            return AssetTimeSeries(asset_symbol=asset_symbol.upper(), series=series_data)
        except Exception as e:
            error_msg = f"Failed to fetch units series for {asset_symbol}: {e}"
            self.logger.error(error_msg)
            raise GreptimeError(error_msg)

    def fetch_price_series(
        self,
        asset_symbol: str,
        date_range: Optional[DateRange] = None
    ) -> Optional[AssetTimeSeries]:
        """
        Fetch per-timestamp price_usd for a single asset from a price table.

        Assumes self.table_prefix points to the appropriate price namespace
        (e.g., 'minswap_prices_' or 'liqwid_prices_') and that the table has
        columns: ts, price_usd.
        """
        table_name = self._get_table_name(asset_symbol)
        # Validate and existence
        if not validate_table_name(table_name, self.table_prefix):
            raise GreptimeQueryError(f"Invalid table name: {table_name}")
        if not self._table_exists(table_name):
            self.logger.warning(f"Table not found for asset={asset_symbol}, skipping price fetch")
            return None
        try:
            date_filter = ""
            if date_range:
                date_filter = build_date_range_filter(date_range.start, date_range.end)
            sql = f"""
            SELECT ts, price_usd
            FROM {table_name}
            {date_filter}
            ORDER BY ts ASC
            """
            result = self._execute_sql(sql)
            records = self._parse_query_response(result, ['ts', 'price_usd'])
            series_data = {}
            for record in records:
                ts_ms = record.get('ts')
                price = record.get('price_usd')
                if ts_ms is None or price is None:
                    continue
                dt = timestamp_to_datetime(int(ts_ms))
                series_data[dt] = safe_float(price)
            return AssetTimeSeries(asset_symbol=asset_symbol.upper(), series=series_data)
        except Exception as e:
            error_msg = f"Failed to fetch price series for {asset_symbol}: {e}"
            self.logger.error(error_msg)
            raise GreptimeError(error_msg)

    def fetch_dual_price_series(
        self,
        asset_symbol: str,
        date_range: Optional[DateRange] = None
    ) -> Optional[tuple[AssetTimeSeries, AssetTimeSeries]]:
        """
        Fetch per-timestamp price_usd AND ada_usd for a single asset from a minswap price table.

        Note: This requires self.table_prefix to point to minswap price tables ('minswap_prices_').
        Returns a tuple (price_usd_series, ada_usd_series) or None if table missing.
        """
        table_name = self._get_table_name(asset_symbol)
        if not validate_table_name(table_name, self.table_prefix):
            raise GreptimeQueryError(f"Invalid table name: {table_name}")
        if not self._table_exists(table_name):
            self.logger.warning(f"Table not found for asset={asset_symbol}, skipping dual price fetch")
            return None
        try:
            date_filter = ""
            if date_range:
                date_filter = build_date_range_filter(date_range.start, date_range.end)
            sql = f"""
            SELECT ts, price_usd, ada_usd
            FROM {table_name}
            {date_filter}
            ORDER BY ts ASC
            """
            result = self._execute_sql(sql)
            records = self._parse_query_response(result, ['ts', 'price_usd', 'ada_usd'])
            series_usd = {}
            series_ada = {}
            for record in records:
                ts_ms = record.get('ts')
                p_usd = record.get('price_usd')
                a_usd = record.get('ada_usd')
                if ts_ms is None:
                    continue
                dt = timestamp_to_datetime(int(ts_ms))
                if p_usd is not None:
                    series_usd[dt] = safe_float(p_usd)
                if a_usd is not None:
                    series_ada[dt] = safe_float(a_usd)
            return (
                AssetTimeSeries(asset_symbol=asset_symbol.upper(), series=series_usd),
                AssetTimeSeries(asset_symbol=asset_symbol.upper(), series=series_ada),
            )
        except Exception as e:
            error_msg = f"Failed to fetch dual price series for {asset_symbol}: {e}"
            self.logger.error(error_msg)
            raise GreptimeError(error_msg)

    def fetch_latest_price_usd(self, asset_symbol: str) -> Optional[float]:
        """
        Fetch the latest price_usd (most recent ts) from the current table_prefix namespace.
        For liqwid, this should be used with the supply positions prefix.
        """
        table_name = self._get_table_name(asset_symbol)
        if not validate_table_name(table_name, self.table_prefix):
            raise GreptimeQueryError(f"Invalid table name: {table_name}")
        if not self._table_exists(table_name):
            return None
        try:
            sql = f"""
            SELECT price_usd
            FROM {table_name}
            ORDER BY ts DESC
            LIMIT 1
            """
            result = self._execute_sql(sql)
            records = self._parse_query_response(result, ['price_usd'])
            if not records:
                return None
            val = records[0].get('price_usd')
            from .utils import safe_float
            return safe_float(val) if val is not None else None
        except Exception as e:
            self.logger.debug(f"fetch_latest_price_usd failed for {asset_symbol}: {e}")
            return None

    
    
    def fetch_asset_series_by_wallet(
        self,
        asset_symbol: str,
        date_range: Optional[DateRange] = None
    ) -> Dict[str, AssetTimeSeries]:
        """
        Fetch time series data for a single asset, grouped by wallet address
        
        This method preserves per-wallet granularity instead of aggregating
        across all wallets. Useful for per-wallet gains calculation.
        
        Args:
            asset_symbol: Asset symbol to fetch
            date_range: Optional date range filter
            
        Returns:
            Dictionary mapping wallet_address to AssetTimeSeries
            Returns empty dict if table doesn't exist
            
        Raises:
            GreptimeError: If query fails
        """
        table_name = self._get_table_name(asset_symbol)
        
        # Validate table name for security
        if not validate_table_name(table_name, self.table_prefix):
            raise GreptimeQueryError(f"Invalid table name: {table_name}")
        
        # Check if table exists
        if not self._table_exists(table_name):
            self.logger.warning(f"Table not found for asset={asset_symbol}, skipping")
            return {}
        
        self.logger.info(f"Fetching per-wallet series for asset={asset_symbol.upper()}")
        
        try:
            # Build date range filter
            date_filter = ""
            if date_range:
                date_filter = build_date_range_filter(date_range.start, date_range.end)
            
            # Build SQL query - preserve wallet_address in GROUP BY
            sql = f"""
            SELECT ts, wallet_address, SUM(COALESCE(usd_value, underlying_units * price_usd, 0)) AS usd_value_sum
            FROM {table_name}
            {date_filter}
            GROUP BY ts, wallet_address
            ORDER BY wallet_address, ts ASC
            """
            
            # Execute query
            result = self._execute_sql(sql)
            
            # Parse results
            records = self._parse_query_response(result, ['ts', 'wallet_address', 'usd_value_sum'])
            
            # Group by wallet address
            wallet_series = {}
            for record in records:
                timestamp_ms = record.get('ts')
                wallet_addr = record.get('wallet_address')
                usd_value = record.get('usd_value_sum')
                
                if timestamp_ms is not None and wallet_addr and usd_value is not None:
                    dt = timestamp_to_datetime(int(timestamp_ms))
                    
                    # Initialize wallet series if first time seeing this wallet
                    if wallet_addr not in wallet_series:
                        wallet_series[wallet_addr] = {}
                    
                    wallet_series[wallet_addr][dt] = safe_float(usd_value)
            
            # Convert to AssetTimeSeries objects
            result_dict = {}
            for wallet_addr, series_data in wallet_series.items():
                result_dict[wallet_addr] = AssetTimeSeries(
                    asset_symbol=asset_symbol.upper(),
                    series=series_data
                )
            
            num_wallets = len(result_dict)
            total_points = sum(len(ts.series) for ts in result_dict.values())
            self.logger.info(
                f"Retrieved {total_points} data points across {num_wallets} wallets "
                f"for {asset_symbol.upper()}"
            )
            
            return result_dict
            
        except Exception as e:
            error_msg = f"Failed to fetch per-wallet series for {asset_symbol}: {e}"
            self.logger.error(error_msg)
            raise GreptimeError(error_msg)
    
    def fetch_all_assets(
        self,
        asset_symbols: List[str],
        date_range: Optional[DateRange] = None
    ) -> Dict[str, AssetTimeSeries]:
        """
        Fetch time series data for multiple assets
        
        Args:
            asset_symbols: List of asset symbols to fetch
            date_range: Optional date range filter
            
        Returns:
            Dictionary mapping asset symbols to their time series
            
        Raises:
            GreptimeError: If critical error occurs
        """
        self.logger.info(f"Fetching data for {len(asset_symbols)} assets")
        
        results = {}
        progress = ProgressTracker(len(asset_symbols), "Fetching asset data")
        
        for asset_symbol in asset_symbols:
            try:
                series = self.fetch_asset_series(asset_symbol, date_range)
                if series:
                    results[series.asset_symbol] = series
                progress.update()
                
            except GreptimeError as e:
                self.logger.error(f"Failed to fetch {asset_symbol}: {e}")
                progress.update()
                # Continue with other assets
                continue
        
        progress.finish()
        
        if not results:
            self.logger.warning("No asset data retrieved")
        else:
            self.logger.info(f"Successfully retrieved data for {len(results)} assets")
        
        return results

    # ================= Transactions (deposits/withdrawals) =================
    def _get_deposits_table(self, asset_symbol: str, deposits_prefix: str) -> str:
        from .utils import normalize_asset_symbol
        normalized = normalize_asset_symbol(asset_symbol)
        return f"{deposits_prefix}{normalized}"

    def _get_withdrawals_table(self, asset_symbol: str, withdrawals_prefix: str) -> str:
        from .utils import normalize_asset_symbol
        normalized = normalize_asset_symbol(asset_symbol)
        return f"{withdrawals_prefix}{normalized}"

    def fetch_transactions(
        self,
        asset_symbol: str,
        deposits_prefix: str,
        withdrawals_prefix: str,
        date_range: Optional[DateRange] = None,
        wallet_address: Optional[str] = None,
    ) -> List[Transaction]:
        """
        Fetch deposit and withdrawal transactions for a single asset from Greptime.

        Returns a merged, timestamp-ordered list of Transaction objects.
        Expected table schema (production): ts, created_at, wallet_address, market_id, amount, notes
        
        Args:
            asset_symbol: Asset symbol to fetch transactions for
            deposits_prefix: Table prefix for deposits
            withdrawals_prefix: Table prefix for withdrawals
            date_range: Optional date range filter
            wallet_address: Optional wallet address filter (for per-wallet queries)
        """
        from .utils import validate_table_name, build_date_range_filter, timestamp_to_datetime, safe_float
        from .utils import normalize_asset_symbol

        deposits_table = self._get_deposits_table(asset_symbol, deposits_prefix)
        withdrawals_table = self._get_withdrawals_table(asset_symbol, withdrawals_prefix)

        # Validate table names for safety
        if not validate_table_name(deposits_table, deposits_prefix):
            raise GreptimeQueryError(f"Invalid deposits table name: {deposits_table}")
        if not validate_table_name(withdrawals_table, withdrawals_prefix):
            raise GreptimeQueryError(f"Invalid withdrawals table name: {withdrawals_table}")

        txs: List[Transaction] = []

        # Helper to run a single-table fetch
        def _fetch_from_table(table: str, tx_type: str) -> None:
            if not self._table_exists(table):
                return
            
            # Build WHERE clause
            where_clauses = []
            if date_range:
                date_filter = build_date_range_filter(date_range.start, date_range.end)
                if date_filter:
                    # Remove "WHERE" if present, we'll add it back
                    date_filter = date_filter.replace("WHERE", "").strip()
                    where_clauses.append(date_filter)
            
            if wallet_address:
                where_clauses.append(f"wallet_address = '{wallet_address}'")
            
            where = ""
            if where_clauses:
                where = "WHERE " + " AND ".join(where_clauses)
            
            sql = (
                f"SELECT ts, created_at, wallet_address, market_id, amount, notes "
                f"FROM {table} {where} ORDER BY ts ASC"
            )
            result = self._execute_sql(sql)
            records = self._parse_query_response(
                result, ["ts", "created_at", "wallet_address", "market_id", "amount", "notes"]
            )
            for r in records:
                ts_ms = r.get("ts")
                created_ms = r.get("created_at")
                wallet = r.get("wallet_address") or ""
                market_id = r.get("market_id") or ""
                amount_val = safe_float(r.get("amount"), 0.0)
                notes = r.get("notes")

                # Convert timestamps
                if ts_ms is None:
                    continue
                ts = timestamp_to_datetime(int(ts_ms))
                created_at = timestamp_to_datetime(int(created_ms)) if created_ms is not None else ts

                # Normalize amount sign for withdrawals (ensure negative)
                if tx_type == "withdrawal" and amount_val > 0:
                    amount_val = -amount_val

                # Build Transaction
                tx = Transaction(
                    timestamp=ts,
                    wallet_address=str(wallet),
                    market_id=str(market_id),
                    asset_symbol=normalize_asset_symbol(asset_symbol),
                    amount=amount_val,
                    transaction_type=tx_type,
                    notes=str(notes) if notes is not None else None,
                    created_at=created_at,
                )
                txs.append(tx)

        # Fetch deposits then withdrawals
        _fetch_from_table(deposits_table, "deposit")
        _fetch_from_table(withdrawals_table, "withdrawal")

        # Sort merged transactions by timestamp
        txs.sort(key=lambda t: t.timestamp)
        return txs

    def _show_tables(self) -> List[str]:
        result = self._execute_sql("SHOW TABLES")
        table_names: List[str] = []
        try:
            outputs = result.get('output', []) if isinstance(result, dict) else []
            for output_block in outputs:
                records = output_block.get('records') if isinstance(output_block, dict) else None
                if not isinstance(records, dict):
                    continue
                rows = records.get('rows', [])
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if isinstance(row, list) and len(row) > 0 and row[0] is not None:
                        table_names.append(str(row[0]))
        except Exception:
            table_names = []
        return table_names
    
    def discover_asset_tables(self) -> List[str]:
        """
        Discover available asset tables in the database
        
        Returns:
            List of asset symbols with available tables
            
        Raises:
            GreptimeError: If discovery query fails
        """
        try:
            self.logger.info("Discovering available asset tables...")
            
            # Use SHOW TABLES to find liqwid_supply_positions_* tables
            table_names = self._show_tables()

            asset_symbols = []
            prefix = str(self.table_prefix or "")
            prefix_lower = prefix.lower()
            for table_name in table_names:
                t = str(table_name or "")
                if not t:
                    continue
                base = t.split(".")[-1]
                base_lower = base.lower()
                if prefix_lower and base_lower.startswith(prefix_lower):
                    asset_symbol = base_lower[len(prefix_lower):]
                    if asset_symbol:
                        asset_symbols.append(asset_symbol)
            
            # Sort for consistent ordering
            asset_symbols = sorted(set(asset_symbols))
            
            self.logger.info(f"Discovered {len(asset_symbols)} asset tables: {', '.join(asset_symbols).upper()}")
            return asset_symbols
            
        except Exception as e:
            error_msg = f"Failed to discover asset tables: {e}"
            self.logger.error(error_msg)
            raise GreptimeError(error_msg)
    
    def discover_wallet_addresses(self, table_prefix: Optional[str] = None) -> List[str]:
        """
        Discover all unique wallet addresses across asset tables.
        
        Queries all tables matching the prefix to extract unique wallet_address values.
        This is useful for automatically discovering which wallets have historical data
        without requiring manual configuration.
        
        Args:
            table_prefix: Optional prefix override (defaults to self.table_prefix)
            
        Returns:
            Sorted list of unique wallet addresses found across all asset tables.
            Returns empty list if no tables found or on error.
            
        Example:
            >>> reader = GreptimeReader(config, "liqwid_supply_positions_")
            >>> wallets = reader.discover_wallet_addresses()
            >>> print(f"Found {len(wallets)} wallets")
        """
        prefix = table_prefix or self.table_prefix
        wallet_addresses = set()
        
        try:
            self.logger.info(f"Discovering wallet addresses from tables with prefix '{prefix}'...")
            
            # First, get all tables matching the prefix
            table_names = self._show_tables()
            
            # Find tables matching our prefix
            matching_tables = []
            prefix_lower = str(prefix or "").lower()
            for table_name in table_names:
                t = str(table_name or "")
                if not t:
                    continue
                base = t.split(".")[-1]
                if prefix_lower and base.lower().startswith(prefix_lower):
                    matching_tables.append(t)
            
            if not matching_tables:
                self.logger.warning(f"No tables found with prefix '{prefix}'")
                return []
            
            self.logger.info(f"Scanning {len(matching_tables)} tables for wallet addresses...")
            
            # Query each table for unique wallet addresses
            for table_name in matching_tables:
                try:
                    # Query for distinct wallet addresses in this table
                    sql = f"SELECT DISTINCT wallet_address FROM {table_name} WHERE wallet_address IS NOT NULL"
                    result = self._execute_sql(sql)
                    records = self._parse_query_response(result, ['wallet_address'])
                    
                    # Collect non-empty wallet addresses
                    for record in records:
                        wallet = record.get('wallet_address', '').strip()
                        if wallet:  # Filter out empty strings
                            wallet_addresses.add(wallet)
                    
                    self.logger.debug(f"Table {table_name}: found {len(records)} wallet entries")
                    
                except Exception as e:
                    # Log but continue with other tables
                    self.logger.warning(f"Failed to query wallet addresses from {table_name}: {e}")
                    continue

            # Convert to sorted list for consistent ordering
            wallet_list = sorted(wallet_addresses)

            self.logger.info(f"Discovered {len(wallet_list)} unique wallet addresses across all tables")
            if wallet_list:
                preview = [f"{w[:20]}..." for w in wallet_list[:5]]
                self.logger.debug(f"Sample wallets: {', '.join(preview)}")

            return wallet_list

        except Exception as e:
            # Don't raise, just log and return empty list to allow fallback
            self.logger.error(f"Failed to discover wallet addresses: {e}")
            return []
    
    def get_data_timespan(self, asset_symbols: List[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Get the overall timespan of available data across assets
        
        Args:
            asset_symbols: Asset symbols to check
            
        Returns:
            Tuple of (earliest_timestamp, latest_timestamp) or (None, None) if no data
        """
        self.logger.info("Determining data timespan across assets")
        
        min_timestamp = None
        max_timestamp = None
        
        for asset_symbol in asset_symbols:
            table_name = self._get_table_name(asset_symbol)
            
            if not self._table_exists(table_name):
                continue
            
            try:
                sql = f"SELECT MIN(ts) as min_ts, MAX(ts) as max_ts FROM {table_name}"
                result = self._execute_sql(sql)
                records = self._parse_query_response(result, ['min_ts', 'max_ts'])
                
                if records and records[0]:
                    min_ts = records[0].get('min_ts')
                    max_ts = records[0].get('max_ts')
                    
                    if min_ts:
                        asset_min = timestamp_to_datetime(int(min_ts))
                        if min_timestamp is None or asset_min < min_timestamp:
                            min_timestamp = asset_min
                    
                    if max_ts:
                        asset_max = timestamp_to_datetime(int(max_ts))
                        if max_timestamp is None or asset_max > max_timestamp:
                            max_timestamp = asset_max
                            
            except Exception as e:
                self.logger.warning(f"Failed to get timespan for {asset_symbol}: {e}")
                continue
        
        if min_timestamp and max_timestamp:
            self.logger.info(f"Data timespan: {min_timestamp} to {max_timestamp}")
        else:
            self.logger.warning("No timespan data available")
        
        return min_timestamp, max_timestamp
    
    def close(self) -> None:
        """Close the reader and cleanup resources"""
        if hasattr(self, 'session'):
            self.session.close()
        self.logger.info("GreptimeDB reader closed")


def create_greptime_reader(config: GreptimeConnConfig, table_prefix: str = "liqwid_supply_positions_") -> GreptimeReader:
    """
    Factory function to create GreptimeDB reader
    
    Args:
        config: GreptimeDB connection configuration
        table_prefix: Table prefix for asset tables
        
    Returns:
        Configured GreptimeReader instance
    """
    return GreptimeReader(config, table_prefix)
