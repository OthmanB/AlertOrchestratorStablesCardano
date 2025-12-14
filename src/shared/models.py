#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Domain Models for Liqwid Client Aggregator
Defines core data structures for time series aggregation and reporting.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class TimePointAssetValue:
    """
    Represents asset values at a specific timestamp
    
    Used for intermediate data processing during aggregation
    """
    timestamp: datetime
    values: Dict[str, float]  # asset_symbol -> usd_value


@dataclass
class AggregatedRow:
    """
    Represents a complete aggregated row for output
    
    Contains timestamp, individual asset values, and total across all assets
    """
    timestamp: datetime
    asset_values: Dict[str, float]  # asset_symbol -> usd_value
    total: float


@dataclass
class GainsRow:
    """
    Represents gains data at a specific timestamp
    
    Contains timestamp, gains values (absolute and percentage), and smoothed variants
    """
    timestamp: datetime
    raw_absolute_gain: float  # USD per unit time
    raw_percentage_gain: float  # Percentage per unit time
    smoothed_absolute_gain: Optional[float] = None
    smoothed_percentage_gain: Optional[float] = None
    reference_value: float = 0.0  # Base value for percentage calculations


@dataclass
class GainStats:
    """
    Statistics for all-time gains calculation
    
    Provides baseline and average gains across the time series.
    None values indicate insufficient data for calculation.
    """
    initial_total: float
    average_percentage_gain: Optional[float] = None
    average_absolute_gain: Optional[float] = None
    
    def __post_init__(self):
        """Validate that initial_total is non-negative"""
        if self.initial_total < 0:
            raise ValueError("initial_total cannot be negative")


@dataclass
class AssetTimeSeries:
    """
    Time series data for a single asset
    
    Maps timestamps to USD values for aggregation processing
    """
    asset_symbol: str
    series: Dict[datetime, float]  # timestamp -> usd_value
    
    def __post_init__(self):
        """Validate asset symbol is non-empty"""
        if not self.asset_symbol or not self.asset_symbol.strip():
            raise ValueError("asset_symbol cannot be empty")


@dataclass
class ProcessingStats:
    """
    Statistics about the data processing operation
    
    Useful for reporting and debugging
    """
    total_timestamps: int
    assets_processed: int
    total_records: int
    timespan_start: Optional[datetime] = None
    timespan_end: Optional[datetime] = None
    missing_data_points: int = 0
    
    def __post_init__(self):
        """Validate counts are non-negative"""
        if any(count < 0 for count in [self.total_timestamps, self.assets_processed, self.total_records, self.missing_data_points]):
            raise ValueError("Counts cannot be negative")


@dataclass
class Transaction:
    """
    Represents a deposit or withdrawal transaction
    
    Tracks manual deposits and withdrawals to enable accurate APY calculation
    by separating principal movements from interest earnings.
    """
    timestamp: datetime
    wallet_address: str
    market_id: str
    asset_symbol: str
    amount: float  # Positive for deposits, negative for withdrawals
    transaction_type: str  # 'deposit' or 'withdrawal'
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __post_init__(self):
        """Validate transaction data"""
        if not self.wallet_address or not self.wallet_address.strip():
            raise ValueError("wallet_address cannot be empty")
        
        if not self.market_id or not self.market_id.strip():
            raise ValueError("market_id cannot be empty")
            
        if not self.asset_symbol or not self.asset_symbol.strip():
            raise ValueError("asset_symbol cannot be empty")
            
        if self.transaction_type not in ['deposit', 'withdrawal']:
            raise ValueError("transaction_type must be 'deposit' or 'withdrawal'")
            
        # Validate amount consistency with transaction type
        if self.transaction_type == 'deposit' and self.amount <= 0:
            raise ValueError("deposit amount must be positive")
            
        if self.transaction_type == 'withdrawal' and self.amount >= 0:
            raise ValueError("withdrawal amount must be negative")


@dataclass
class WalletBreakdown:
    """
    Per-wallet Wmax breakdown for multi-wallet asset decisions
    
    Tracks the maximum allowable withdrawal (wmax_usd) for each individual
    wallet address contributing to an asset's total position.
    """
    wallet_address: str
    wmax_usd: float
    v_t1_usd: float
    
    def abbreviated_address(self) -> str:
        """
        Return abbreviated Cardano address format: addr1qxytz...5ur5m
        (first 11 characters + last 6 characters)
        
        Returns:
            Abbreviated address string, or full address if <= 17 chars
        """
        if len(self.wallet_address) <= 17:
            return self.wallet_address
        return f"{self.wallet_address[:11]}...{self.wallet_address[-6:]}"
    
    def __post_init__(self):
        """Validate wallet breakdown data"""
        if not self.wallet_address or not self.wallet_address.strip():
            raise ValueError("wallet_address cannot be empty")
        
        if self.wmax_usd < 0:
            raise ValueError("wmax_usd cannot be negative")

        if self.v_t1_usd < 0:
            raise ValueError("v_t1_usd cannot be negative")


@dataclass
class AdjustedSupplyPosition:
    """
    Supply position adjusted for deposits/withdrawals using correct formula
    
    Provides the foundation for accurate APY calculations by separating
    raw supply positions from manual principal movements using the verified
    formula: G(t) = P(t) - P(t₀) - CDF(D(t,t₀)) + CDF(W(t,t₀))
    """
    timestamp: datetime
    asset_symbol: str
    raw_position: float
    cumulative_deposits: float
    cumulative_withdrawals: float
    adjusted_position: float  # True gain using correct formula
    net_gain: float  # Same as adjusted_position (true gain)
    
    def __post_init__(self):
        """Validate adjusted position calculations"""
        if not self.asset_symbol or not self.asset_symbol.strip():
            raise ValueError("asset_symbol cannot be empty")
            
        if self.raw_position < 0:
            raise ValueError("raw_position cannot be negative")
            
        if self.cumulative_deposits < 0:
            raise ValueError("cumulative_deposits cannot be negative")
            
        if self.cumulative_withdrawals > 0:
            raise ValueError("cumulative_withdrawals must be negative or zero")


# ===== Additional dataclasses used by API clients =====

@dataclass
class Market:
    """Liqwid market metadata used by GraphQL client"""
    id: str
    name: str
    underlying_symbol: str
    underlying_decimals: int
    underlying_price: Optional[float] = None
    qtoken_policy: Optional[str] = None
    qtoken_symbol: Optional[str] = None
    qtoken_decimals: Optional[int] = None
    exchange_rate: Optional[float] = None


@dataclass
class PricePoint:
    """Simple price point for an asset symbol"""
    symbol: str
    price: float
    timestamp: datetime


@dataclass
class WalletAsset:
    """Koios wallet asset entry used by Koios client"""
    policy_id: str
    asset_name: str
    fingerprint: str
    decimals: int
    quantity: int
