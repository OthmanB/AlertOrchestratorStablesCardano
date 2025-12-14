#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transaction Syncer for Liqwid Deposits and Withdrawals

Synchronizes transaction history from Liqwid GraphQL API to Greptime database.
Handles deduplication, batch writes, and error reporting.

Usage:
    syncer = TransactionSyncer(liqwid_client, greptime_reader, greptime_writer, logger)
    report = syncer.sync_wallet(wallet_address, assets)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from datetime import datetime, UTC
import logging

from ..shared.liqwid_client import LiqwidClient
from ..shared.greptime_reader import GreptimeReader
from ..shared.greptime_writer import GreptimeWriter
from ..shared.models import Transaction
from ..shared.resolver import Resolver


@dataclass
class SyncReport:
    """Report of sync operation results"""
    wallet_address: str
    timestamp: datetime
    total_liqwid_txs: int
    total_greptime_txs: int
    new_deposits: int
    new_withdrawals: int
    errors: List[str] = field(default_factory=list)
    new_transactions: List[Dict] = field(default_factory=list)
    skipped_duplicates: int = 0
    skipped_unknown_assets: int = 0
    
    @property
    def success(self) -> bool:
        """Sync succeeded if no errors occurred"""
        return len(self.errors) == 0
    
    @property
    def total_new(self) -> int:
        """Total new transactions synced"""
        return self.new_deposits + self.new_withdrawals
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'total_liqwid_txs': self.total_liqwid_txs,
            'total_greptime_txs': self.total_greptime_txs,
            'new_deposits': self.new_deposits,
            'new_withdrawals': self.new_withdrawals,
            'total_new': self.total_new,
            'errors': self.errors,
            'success': self.success,
            'skipped_duplicates': self.skipped_duplicates,
            'skipped_unknown_assets': self.skipped_unknown_assets
        }


class TransactionSyncer:
    """
    Syncs deposits/withdrawals from Liqwid API to Greptime.
    
    Handles:
    - Fetching transactions from Liqwid API
    - Deduplication by transaction ID
    - Asset symbol normalization
    - Batch writing to Greptime
    - Error reporting per transaction
    """
    
    def __init__(
        self,
        liqwid_client: LiqwidClient,
        greptime_reader: GreptimeReader,
        greptime_writer: GreptimeWriter,
        logger: logging.Logger,
        reference_keyword: str = "alert_driven"
    ):
        """
        Initialize TransactionSyncer.
        
        Args:
            liqwid_client: Client for Liqwid GraphQL API
            greptime_reader: Reader for existing transactions
            greptime_writer: Writer for new transactions
            logger: Logger instance
            reference_keyword: Keyword to include in transaction notes for reference detection
        """
        self.liqwid = liqwid_client
        self.reader = greptime_reader
        self.writer = greptime_writer
        self.logger = logger
        self.reference_keyword = reference_keyword
        self._resolver = Resolver(greptime_reader=self.reader)
    
    def sync_wallet(
        self,
        wallet_address: str,
        assets: List[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> SyncReport:
        """
        Sync transactions for a wallet across all configured assets.
        
        Args:
            wallet_address: Cardano address (addr1...)
            assets: List of asset symbols to sync
            start_date: Optional start date (ISO format with Z)
            end_date: Optional end date (ISO format with Z)
        
        Returns:
            SyncReport with sync results
        """
        self.logger.info(f"Starting sync for wallet {wallet_address[:20]}...")
        
        # Initialize report
        report = SyncReport(
            wallet_address=wallet_address,
            timestamp=datetime.now(UTC),
            total_liqwid_txs=0,
            total_greptime_txs=0,
            new_deposits=0,
            new_withdrawals=0
        )
        
        try:
            # Step 1: Fetch transactions from Liqwid
            liqwid_result = self._fetch_from_liqwid(wallet_address, start_date, end_date)
            
            if liqwid_result['status'] != 'success':
                report.errors.append(f"Liqwid API error: {liqwid_result.get('error', 'Unknown error')}")
                return report
            
            liqwid_txs = liqwid_result['transactions']
            report.total_liqwid_txs = len(liqwid_txs)
            
            self.logger.info(f"Fetched {len(liqwid_txs)} transactions from Liqwid")
            
            if len(liqwid_txs) == 0:
                self.logger.info("No transactions found in Liqwid for this wallet")
                return report
            
            # Step 2: Fetch existing transactions from Greptime
            greptime_txs = self._fetch_from_greptime(wallet_address, assets)
            report.total_greptime_txs = len(greptime_txs)
            
            self.logger.info(f"Found {len(greptime_txs)} existing transactions in Greptime")
            
            # Step 3: Find new transactions (deduplication)
            new_txs, stats = self._find_delta(liqwid_txs, greptime_txs, assets)
            report.skipped_duplicates = stats['duplicates']
            report.skipped_unknown_assets = stats['unknown_assets']
            
            self.logger.info(f"Found {len(new_txs)} new transactions to sync "
                           f"(skipped {stats['duplicates']} duplicates, "
                           f"{stats['unknown_assets']} unknown assets)")
            
            if len(new_txs) == 0:
                self.logger.info("No new transactions to sync")
                return report
            
            # Step 4: Write new transactions to Greptime
            write_result = self._write_to_greptime(new_txs, wallet_address)
            
            report.new_deposits = write_result['deposits_written']
            report.new_withdrawals = write_result['withdrawals_written']
            if write_result['errors']:
                report.errors.extend(write_result['errors'])
            report.new_transactions = new_txs
            
            self.logger.info(f"Sync complete: {report.new_deposits} deposits, "
                           f"{report.new_withdrawals} withdrawals written")
            
            return report
            
        except Exception as e:
            self.logger.error(f"Sync failed with exception: {e}", exc_info=True)
            report.errors.append(f"Sync exception: {str(e)}")
            return report
    
    def _fetch_from_liqwid(
        self,
        wallet_address: str,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> Dict:
        """
        Fetch transactions from Liqwid API.
        
        Args:
            wallet_address: Cardano address
            start_date: ISO date with Z suffix or None
            end_date: ISO date with Z suffix or None
        
        Returns:
            Result dictionary from LiqwidClient.fetch_historical_transactions()
        """
        return self.liqwid.fetch_historical_transactions(
            wallet_address=wallet_address,
            start_date=start_date,
            end_date=end_date
        )
    
    def _fetch_from_greptime(
        self,
        wallet_address: str,
        assets: List[str],
        deposits_prefix: str = "liqwid_deposits_",
        withdrawals_prefix: str = "liqwid_withdrawals_"
    ) -> List[Transaction]:
        """
        Fetch existing transactions from Greptime for deduplication.
        
        Args:
            wallet_address: Cardano address
            assets: List of asset symbols
            deposits_prefix: Table prefix for deposits
            withdrawals_prefix: Table prefix for withdrawals
        
        Returns:
            List of existing Transaction objects
        """
        existing_txs = []
        
        for asset in assets:
            asset_lower = asset.lower()
            
            try:
                # Fetch all transactions for this asset
                transactions = self.reader.fetch_transactions(
                    asset_symbol=asset_lower,
                    deposits_prefix=deposits_prefix,
                    withdrawals_prefix=withdrawals_prefix,
                    date_range=None,  # Get all transactions
                    wallet_address=wallet_address
                )
                existing_txs.extend(transactions)
                
            except Exception as e:
                self.logger.warning(f"Failed to fetch existing transactions for {asset}: {e}")
                continue
        
        return existing_txs
    
    def _find_delta(
        self,
        liqwid_txs: List[Dict],
        greptime_txs: List[Transaction],
        assets: List[str]
    ) -> tuple[List[Dict], Dict[str, int]]:
        """
        Find new transactions by comparing Liqwid and Greptime data.
        
        Deduplication strategy:
        - Use transaction 'id' field (transaction hash) as primary key
        - Skip transactions already in Greptime
        - Skip transactions for assets not in configured list
        
        Args:
            liqwid_txs: Transactions from Liqwid API
            greptime_txs: Existing transactions from Greptime
            assets: List of configured asset symbols
        
        Returns:
            Tuple of (new_transactions, stats_dict)
            stats_dict contains: 'duplicates', 'unknown_assets'
        """
        # Build set of existing transaction IDs from Transaction.notes field
        # Note: We store the Liqwid transaction ID in the notes field
        existing_ids: Set[str] = set()
        for tx in greptime_txs:
            if tx.notes:
                # Extract tx ID from notes like "Synced from Liqwid (tx: abc123...)"
                if 'tx:' in tx.notes:
                    tx_id = tx.notes.split('tx: ')[1].split(')')[0].replace('...', '')
                    existing_ids.add(tx_id)
        
        # Build resolved asset set upfront for efficient comparison
        # Use Resolver to map bridged assets (wanUSDC) to base symbols (usdc)
        resolved_asset_set = set()
        for asset in assets:
            try:
                _mid, sym = self._resolver.resolve_asset(asset)
                resolved_asset_set.add(sym.lower())
            except Exception:
                # If resolution fails, use original (for direct matches like DJED)
                resolved_asset_set.add(asset.lower())
        
        new_txs = []
        duplicates = 0
        unknown_assets = 0
        
        for tx in liqwid_txs:
            tx_id = tx['id']
            display_name = tx['displayName']  # e.g., "wanUSDC", "DJED", "wanUSDT"
            
            # Check if already exists (check full ID or prefix)
            is_duplicate = False
            for existing_id in existing_ids:
                if tx_id.startswith(existing_id) or existing_id.startswith(tx_id[:16]):
                    is_duplicate = True
                    break
            
            if is_duplicate:
                duplicates += 1
                continue
            
            # Resolve display name to normalized symbol using Resolver
            try:
                market_id, resolved_sym = self._resolver.resolve_asset(display_name)
                asset_symbol = resolved_sym.lower()
            except Exception:
                # If resolution fails, use original name (for direct matches)
                # Use uppercase displayName as market_id (e.g., "DJED", "USDM")
                market_id = display_name.upper()
                asset_symbol = display_name.lower()
            
            # Check if resolved asset is configured
            if asset_symbol not in resolved_asset_set:
                unknown_assets += 1
                self.logger.debug(
                    f"Skipping transaction for unknown asset: {display_name} "
                    f"(resolved to {asset_symbol}, configured: {resolved_asset_set})"
                )
                continue
            
            # Attach resolved metadata to transaction for later use
            tx['_resolved_market_id'] = market_id
            tx['_resolved_asset_symbol'] = asset_symbol
            
            new_txs.append(tx)
        
        stats = {
            'duplicates': duplicates,
            'unknown_assets': unknown_assets
        }
        
        return new_txs, stats
    
    def _write_to_greptime(
        self,
        transactions: List[Dict],
        wallet_address: str
    ) -> Dict:
        """
        Write new transactions to Greptime.
        
        Args:
            transactions: List of new transactions to write
            wallet_address: Cardano address
        
        Returns:
            Dictionary with:
                - deposits_written: int
                - withdrawals_written: int
                - errors: List[str]
        """
        deposits_written = 0
        withdrawals_written = 0
        errors = []
        
        # Group transactions by type for batch writing
        deposits = []
        withdrawals = []
        
        for tx in transactions:
            try:
                # Map Liqwid type to our type
                tx_type = tx['type']
                if tx_type == 'SUPPLY':
                    our_type = 'deposit'
                    amount = abs(float(tx['amount']))  # Positive for deposits
                elif tx_type == 'WITHDRAW':
                    our_type = 'withdrawal'
                    amount = -abs(float(tx['amount']))  # Negative for withdrawals
                else:
                    errors.append(f"Unknown transaction type: {tx_type} for tx {tx['id'][:16]}")
                    continue
                
                # Use resolved metadata attached in _find_delta
                market_id = tx.get('_resolved_market_id', tx['displayName'].upper())
                asset_symbol = tx.get('_resolved_asset_symbol', tx['displayName'].lower())
                
                # Parse timestamp
                timestamp_str = tx['time']
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1]  # Remove Z for parsing
                timestamp = datetime.fromisoformat(timestamp_str)
                
                # Create Transaction model with reference keyword prefix
                # Format: "{keyword} Synced from Liqwid (tx: {id}...)"
                # This ensures synced transactions are recognized as reference points
                # for gains calculation (both deposits and withdrawals)
                notes = f"{self.reference_keyword} Synced from Liqwid (tx: {tx['id'][:16]}...)"
                
                transaction = Transaction(
                    timestamp=timestamp,
                    wallet_address=wallet_address,
                    market_id=market_id,  # Use resolved market_id (e.g., "USDC", "DJED")
                    asset_symbol=asset_symbol,
                    amount=amount,
                    transaction_type=our_type,
                    notes=notes
                )
                
                if our_type == 'deposit':
                    deposits.append(transaction)
                else:
                    withdrawals.append(transaction)
                    
            except Exception as e:
                error_msg = f"Failed to prepare transaction {tx.get('id', 'unknown')[:16]}: {str(e)}"
                self.logger.error(error_msg)
                errors.append(error_msg)
                continue
        
        # Batch write deposits
        if deposits:
            try:
                result = self.writer.insert_transactions(deposits, "deposit")
                deposits_written = sum(result.values())
                self.logger.info(f"Wrote {deposits_written} deposits")
            except Exception as e:
                error_msg = f"Failed to write deposits: {str(e)}"
                self.logger.error(error_msg)
                errors.append(error_msg)
        
        # Batch write withdrawals
        if withdrawals:
            try:
                result = self.writer.insert_transactions(withdrawals, "withdrawal")
                withdrawals_written = sum(result.values())
                self.logger.info(f"Wrote {withdrawals_written} withdrawals")
            except Exception as e:
                error_msg = f"Failed to write withdrawals: {str(e)}"
                self.logger.error(error_msg)
                errors.append(error_msg)
        
        return {
            'deposits_written': deposits_written,
            'withdrawals_written': withdrawals_written,
            'errors': errors
        }
