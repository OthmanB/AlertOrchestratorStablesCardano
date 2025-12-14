#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core Aggregation Engine for Liqwid Client Aggregator
Handles time series aggregation, gap filling, and gain calculations.
"""

import logging
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Set
from collections import defaultdict

from .models import AssetTimeSeries, AggregatedRow, GainStats, ProcessingStats, Transaction
from .utils import safe_float, calculate_percentage_change
from .correct_calculations import calculate_correct_gains


class AggregationError(Exception):
    """Aggregation-related errors"""
    pass


class TimeSeriesAggregator:
    """
    Aggregates multi-asset time series data with gap filling and gain calculations
    
    Handles:
    - Timestamp alignment across multiple assets
    - Missing data gap filling (0 values)
    - Baseline detection and gain statistics
    - Extension points for future net flow adjustments
    """
    
    def __init__(self, fill_missing_with_zero: bool = True):
        """
        Initialize aggregator
        
        Args:
            fill_missing_with_zero: Whether to fill missing asset values with 0
        """
        self.fill_missing_with_zero = fill_missing_with_zero
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def aggregate_series(
        self, 
        asset_series: Dict[str, AssetTimeSeries]
    ) -> List[AggregatedRow]:
        """
        Aggregate multiple asset time series into unified rows
        
        Args:
            asset_series: Dictionary mapping asset symbols to their time series
            
        Returns:
            List of aggregated rows sorted by timestamp
            
        Raises:
            AggregationError: If aggregation fails
        """
        if not asset_series:
            self.logger.warning("No asset series provided for aggregation")
            return []
        
        try:
            self.logger.info(f"Aggregating {len(asset_series)} asset series")
            
            # Collect all unique timestamps
            all_timestamps: Set[datetime] = set()
            for series in asset_series.values():
                all_timestamps.update(series.series.keys())
            
            if not all_timestamps:
                self.logger.warning("No timestamps found across all series")
                return []
            
            # Sort timestamps for consistent processing
            sorted_timestamps = sorted(all_timestamps)
            asset_symbols = sorted(asset_series.keys())
            
            self.logger.info(f"Processing {len(sorted_timestamps)} timestamps across {len(asset_symbols)} assets")
            
            # Build aggregated rows
            aggregated_rows = []
            for timestamp in sorted_timestamps:
                asset_values = {}
                total = 0.0
                
                # Collect values for each asset at this timestamp
                for asset_symbol in asset_symbols:
                    series = asset_series[asset_symbol]
                    value = series.series.get(timestamp, 0.0 if self.fill_missing_with_zero else None)
                    
                    if value is not None:
                        asset_values[asset_symbol] = safe_float(value)
                        total += safe_float(value)
                    else:
                        # Don't include assets with missing data if not filling with zero
                        continue
                
                # Create aggregated row
                row = AggregatedRow(
                    timestamp=timestamp,
                    asset_values=asset_values,
                    total=total
                )
                aggregated_rows.append(row)
            
            self.logger.info(f"Successfully aggregated {len(aggregated_rows)} rows")
            return aggregated_rows
            
        except Exception as e:
            error_msg = f"Failed to aggregate series: {e}"
            self.logger.error(error_msg)
            raise AggregationError(error_msg)
    
    def compute_gain_stats(
        self, 
        rows: List[AggregatedRow],
        transactions: Optional[List[Transaction]] = None,
        exclude_zero_baseline: bool = True
    ) -> GainStats:
        """
        Compute gain statistics using correct calculations when transactions are provided
        
        Args:
            rows: List of aggregated rows sorted by timestamp
            transactions: List of transactions for correct gain calculations (optional)
            exclude_zero_baseline: Whether to exclude zero totals when finding baseline
            
        Returns:
            GainStats with baseline and correct gains
            
        Raises:
            AggregationError: If gain calculation fails
        """
        if not rows:
            self.logger.warning("No rows provided for gain calculation")
            return GainStats(initial_total=0.0)
        
        try:
            self.logger.info(f"Computing gain statistics for {len(rows)} rows")
            
            # Find baseline (first non-zero total if excluding zeros)
            initial_total = 0.0
            baseline_found = False
            
            for row in rows:
                if exclude_zero_baseline and row.total <= 0:
                    continue
                initial_total = row.total
                baseline_found = True
                break
            
            if not baseline_found:
                # Use first row's total even if zero
                initial_total = rows[0].total
                self.logger.warning(f"No non-zero baseline found, using first total: {initial_total}")
            
            # Use correct calculations if transactions are provided
            if transactions:
                self.logger.info("Using correct gain calculations with transaction data")
                
                # Extract timestamps and position values
                timestamps = [row.timestamp for row in rows]
                position_values = [row.total for row in rows]
                
                # Calculate correct gains
                align_method = str(getattr(getattr(self, 'config', None), 'alignment_method', 'none'))
                timebase, positions_calc, deposits_cdf, withdrawals_cdf, correct_gains = calculate_correct_gains(
                    position_timestamps=timestamps,
                    position_values=position_values,
                    transactions=transactions,
                    reference_time_index=0,
                    interpolation_method="linear",
                    alignment_method=align_method
                )
                
                # Calculate statistics based on correct gains
                absolute_gains = correct_gains
                percentage_gains = []
                
                for gain in correct_gains:
                    if initial_total > 0:
                        pct_gain = (gain / initial_total) * 100
                        percentage_gains.append(pct_gain)
                
            else:
                # Fallback to naive calculations (for backward compatibility with zero transactions)
                self.logger.info("Using naive gain calculations (no transactions provided)")
                
                absolute_gains = []
                percentage_gains = []
                
                for row in rows:
                    # Absolute gain (naive)
                    abs_gain = row.total - initial_total
                    absolute_gains.append(abs_gain)
                    
                    # Percentage gain (only if baseline is non-zero)
                    if initial_total > 0:
                        pct_gain = ((row.total / initial_total) - 1) * 100
                        percentage_gains.append(pct_gain)
            
            # Calculate averages
            avg_absolute_gain = None
            avg_percentage_gain = None
            
            if len(absolute_gains) > 0:
                # Normalize to a plain Python list via numpy for consistency
                absolute_gains_list = np.asarray(absolute_gains, dtype=float).tolist()
                avg_absolute_gain = sum(absolute_gains_list) / len(absolute_gains_list)
            
            if len(percentage_gains) > 0:
                # Normalize to a plain Python list via numpy for consistency
                percentage_gains_list = np.asarray(percentage_gains, dtype=float).tolist()
                avg_percentage_gain = sum(percentage_gains_list) / len(percentage_gains_list)
            
            # Log results
            if avg_percentage_gain is not None:
                self.logger.info(f"Baseline: ${initial_total:,.2f}, Avg % gain: {avg_percentage_gain:.2f}%")
            else:
                self.logger.info(f"Baseline: ${initial_total:,.2f}, % gains unavailable (zero baseline)")
            
            return GainStats(
                initial_total=initial_total,
                average_percentage_gain=avg_percentage_gain,
                average_absolute_gain=avg_absolute_gain
            )
            
        except Exception as e:
            error_msg = f"Failed to compute gain statistics: {e}"
            self.logger.error(error_msg)
            raise AggregationError(error_msg)
    
    def adjust_for_flows(
        self, 
        rows: List[AggregatedRow], 
        flows: Optional[Dict[datetime, float]] = None
    ) -> List[AggregatedRow]:
        """
        Adjust aggregated rows for net deposit/withdrawal flows
        
        This is a placeholder for future enhancement when flow tracking is implemented.
        Currently returns rows unchanged.
        
        Args:
            rows: Original aggregated rows
            flows: Optional mapping of timestamps to net flow amounts (+ deposits, - withdrawals)
            
        Returns:
            Adjusted rows (currently unchanged)
        """
        if flows is None:
            self.logger.debug("No flows provided, returning original rows")
            return rows
        
        # Future implementation will:
        # 1. Apply flow adjustments to totals
        # 2. Recalculate asset proportions if needed
        # 3. Update gain calculations to account for flows
        
        self.logger.warning("Flow adjustment not yet implemented, returning original rows")
        return rows
    
    def generate_processing_stats(
        self, 
        asset_series: Dict[str, AssetTimeSeries],
        aggregated_rows: List[AggregatedRow]
    ) -> ProcessingStats:
        """
        Generate statistics about the processing operation
        
        Args:
            asset_series: Original asset series data
            aggregated_rows: Resulting aggregated rows
            
        Returns:
            ProcessingStats with operation details
        """
        try:
            # Collect all timestamps from original data
            all_original_timestamps = set()
            total_original_records = 0
            
            for series in asset_series.values():
                all_original_timestamps.update(series.series.keys())
                total_original_records += len(series.series)
            
            # Calculate timespan
            timespan_start = min(all_original_timestamps) if all_original_timestamps else None
            timespan_end = max(all_original_timestamps) if all_original_timestamps else None
            
            # Count missing data points (if we filled with zeros)
            expected_data_points = len(all_original_timestamps) * len(asset_series)
            missing_data_points = expected_data_points - total_original_records
            
            stats = ProcessingStats(
                total_timestamps=len(aggregated_rows),
                assets_processed=len(asset_series),
                total_records=total_original_records,
                timespan_start=timespan_start,
                timespan_end=timespan_end,
                missing_data_points=max(0, missing_data_points)
            )
            
            self.logger.info(f"Processing stats: {stats.total_timestamps} timestamps, "
                           f"{stats.assets_processed} assets, {stats.missing_data_points} missing points")
            
            return stats
            
        except Exception as e:
            self.logger.error(f"Failed to generate processing stats: {e}")
            # Return minimal stats on error
            return ProcessingStats(
                total_timestamps=len(aggregated_rows),
                assets_processed=len(asset_series),
                total_records=0
            )


def aggregate_asset_data(
    asset_series: Dict[str, AssetTimeSeries],
    compute_gains: bool = True,
    exclude_zero_baseline: bool = True
) -> tuple[List[AggregatedRow], Optional[GainStats], ProcessingStats]:
    """
    High-level function to aggregate asset data and compute statistics
    
    Args:
        asset_series: Dictionary mapping asset symbols to their time series
        compute_gains: Whether to compute gain statistics
        exclude_zero_baseline: Whether to exclude zero totals when finding baseline
        
    Returns:
        Tuple of (aggregated_rows, gain_stats, processing_stats)
        
    Raises:
        AggregationError: If aggregation fails
    """
    aggregator = TimeSeriesAggregator()
    
    # Perform aggregation
    aggregated_rows = aggregator.aggregate_series(asset_series)
    
    # Compute gain statistics if requested
    gain_stats = None
    if compute_gains and aggregated_rows:
        gain_stats = aggregator.compute_gain_stats(
            rows=aggregated_rows, 
            transactions=None,  # No transactions available in this context
            exclude_zero_baseline=exclude_zero_baseline
        )
    
    # Generate processing statistics
    processing_stats = aggregator.generate_processing_stats(asset_series, aggregated_rows)
    
    return aggregated_rows, gain_stats, processing_stats


def validate_aggregation_input(asset_series: Dict[str, AssetTimeSeries]) -> None:
    """
    Validate input data for aggregation
    
    Args:
        asset_series: Asset series to validate
        
    Raises:
        AggregationError: If validation fails
    """
    if not asset_series:
        raise AggregationError("No asset series provided")
    
    for asset_symbol, series in asset_series.items():
        if not isinstance(series, AssetTimeSeries):
            raise AggregationError(f"Invalid series type for {asset_symbol}")
        
        if not series.asset_symbol:
            raise AggregationError(f"Empty asset symbol in series")
        
        if not isinstance(series.series, dict):
            raise AggregationError(f"Invalid series data for {asset_symbol}")


def get_aggregation_summary(
    aggregated_rows: List[AggregatedRow],
    gain_stats: Optional[GainStats] = None
) -> str:
    """
    Generate a human-readable summary of aggregation results
    
    Args:
        aggregated_rows: Aggregated data rows
        gain_stats: Optional gain statistics
        
    Returns:
        Formatted summary string
    """
    if not aggregated_rows:
        return "No aggregated data available"
    
    summary_lines = []
    
    # Basic stats
    summary_lines.append(f"📊 Aggregation Summary:")
    summary_lines.append(f"  Timestamps: {len(aggregated_rows)}")
    
    if aggregated_rows:
        first_row = aggregated_rows[0]
        last_row = aggregated_rows[-1]
        
        summary_lines.append(f"  Assets: {len(first_row.asset_values)}")
        summary_lines.append(f"  Time range: {first_row.timestamp} to {last_row.timestamp}")
        summary_lines.append(f"  Initial total: ${first_row.total:,.2f}")
        summary_lines.append(f"  Final total: ${last_row.total:,.2f}")
    
    # Gain stats if available
    if gain_stats:
        summary_lines.append(f"💰 Gain Statistics:")
        summary_lines.append(f"  Baseline: ${gain_stats.initial_total:,.2f}")
        
        if gain_stats.average_percentage_gain is not None:
            summary_lines.append(f"  Avg % gain: {gain_stats.average_percentage_gain:.2f}%")
        
        if gain_stats.average_absolute_gain is not None:
            summary_lines.append(f"  Avg $ gain: ${gain_stats.average_absolute_gain:,.2f}")
    
    return "\n".join(summary_lines)
