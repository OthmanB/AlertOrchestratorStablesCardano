#!/usr/bin/env python3
"""
Time-based aggregation for dashboard plots (performance optimization).
Does NOT affect decision logic, only visualization.

This module provides aggregation functions to reduce the number of data points
displayed on dashboard plots while preserving statistical information through
percentile-based whisker plots.
"""
from __future__ import annotations

import numpy as np
from datetime import datetime, timedelta
from typing import Tuple, List, Optional, Dict
import logging

log = logging.getLogger(__name__)


def aggregate_timeseries(
    timestamps: np.ndarray,
    values: np.ndarray,
    time_unit: str = "5min",
    percentiles: Optional[List[float]] = None
) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """
    Aggregate time series data into fixed time bins with statistical summaries.
    
    This function groups data into time buckets and computes percentiles for each bucket,
    enabling whisker plot visualization that shows data distribution without plotting
    every individual point.
    
    Uses pure numpy implementation (no pandas dependency).
    
    Args:
        timestamps: Array of datetime objects (must be timezone-aware)
        values: Array of numeric values (same length as timestamps)
        time_unit: Aggregation interval - one of:
            "1min", "5min", "15min", "30min", "1h", "6h", "12h", "1d", "3d", "1w"
        percentiles: List of percentile values to compute (0-100 range)
            Default: [10, 25, 50, 75, 90]
    
    Returns:
        Tuple of (bin_centers, stats_dict) where:
        - bin_centers: Array of datetime objects representing center of each time bin
        - stats_dict: Dictionary with keys like 'p10', 'p25', 'p50', 'p75', 'p90', 'count'
            Each value is an ndarray of statistics for each bin
    
    Edge Cases:
        - 0 points in a bin: Bin is skipped (NaN values dropped)
        - 1 point in a bin: All percentiles equal that single value
        - Empty input: Returns empty arrays
    
    Example:
        >>> timestamps = np.array([datetime(2025,11,22,10,0), datetime(2025,11,22,10,5)])
        >>> values = np.array([100.0, 105.0])
        >>> bin_centers, stats = aggregate_timeseries(timestamps, values, "5min")
        >>> stats['p50']  # Median values per 5-minute bin
        array([100., 105.])
    """
    if percentiles is None:
        percentiles = [10.0, 25.0, 50.0, 75.0, 90.0]
    
    # Validate inputs
    if len(timestamps) == 0 or len(values) == 0:
        log.warning("Empty input to aggregate_timeseries, returning empty results")
        return np.array([]), {}
    
    if len(timestamps) != len(values):
        raise ValueError(f"Length mismatch: {len(timestamps)} timestamps vs {len(values)} values")
    
    # Map time_unit to timedelta
    time_delta_map = {
        "1min": timedelta(minutes=1),
        "5min": timedelta(minutes=5),
        "15min": timedelta(minutes=15),
        "30min": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
        "1w": timedelta(weeks=1)
    }
    
    if time_unit not in time_delta_map:
        log.warning(f"Invalid time_unit '{time_unit}', falling back to '5min'")
        time_unit = "5min"
    
    bin_width = time_delta_map[time_unit]
    
    try:
        # Sort by timestamp
        sort_idx = np.argsort(timestamps)
        timestamps_sorted = timestamps[sort_idx]
        values_sorted = values[sort_idx]
        
        # Determine time range and create bins
        t_min = timestamps_sorted[0]
        t_max = timestamps_sorted[-1]
        
        # Calculate number of bins needed
        total_duration = (t_max - t_min).total_seconds()
        bin_width_seconds = bin_width.total_seconds()
        n_bins = int(np.ceil(total_duration / bin_width_seconds)) + 1
        
        # Create bin edges
        bin_edges = [t_min + i * bin_width for i in range(n_bins + 1)]
        
        # Assign each timestamp to a bin
        bin_indices = []
        for ts in timestamps_sorted:
            # Find which bin this timestamp belongs to
            bin_idx = int((ts - t_min).total_seconds() / bin_width_seconds)
            bin_indices.append(bin_idx)
        
        bin_indices = np.array(bin_indices)
        
        # Group values by bin and compute statistics
        unique_bins = np.unique(bin_indices)
        
        bin_centers_list = []
        stats = {f'p{int(p)}': [] for p in percentiles}
        stats['count'] = []
        
        for bin_idx in unique_bins:
            mask = bin_indices == bin_idx
            bin_values = values_sorted[mask]
            
            if len(bin_values) == 0:
                continue  # Skip empty bins
            
            # Bin center is the start of the bin
            bin_start = t_min + bin_idx * bin_width
            bin_centers_list.append(bin_start)
            
            # Compute percentiles
            for p in percentiles:
                percentile_val = np.percentile(bin_values, p)
                stats[f'p{int(p)}'].append(percentile_val)
            
            # Count
            stats['count'].append(len(bin_values))
        
        # Convert lists to arrays
        bin_centers = np.array(bin_centers_list)
        for key in stats:
            stats[key] = np.array(stats[key])
        
        log.info(f"Aggregated {len(timestamps)} points into {len(bin_centers)} bins using {time_unit} intervals")
        
        return bin_centers, stats
        
    except Exception as e:
        log.error(f"Aggregation failed: {e}", exc_info=True)
        # Return empty results on error
        return np.array([]), {}


def should_aggregate(
    n_points: int,
    time_span_days: float,
    threshold_points: int = 1000
) -> bool:
    """
    Determine if aggregation should be applied based on data density.
    
    NOTE: Based on user clarifications, this function is provided for reference
    but should NOT be used for auto-triggering aggregation. Users control
    aggregation manually via dashboard checkbox.
    
    Args:
        n_points: Number of data points in the series
        time_span_days: Time span covered by the data in days
        threshold_points: Point count above which aggregation is recommended
            Default: 1000
    
    Returns:
        True if aggregation is recommended (n_points > threshold_points)
    
    Example:
        >>> should_aggregate(5000, 7.0)  # 5000 points over 7 days
        True
        >>> should_aggregate(500, 1.0)   # 500 points over 1 day
        False
    """
    return n_points > threshold_points


def recommend_time_unit(n_points: int, time_span_days: float) -> str:
    """
    Recommend an appropriate aggregation time unit based on data characteristics.
    
    This is a helper function to suggest sensible defaults, but users should
    always have final control via dashboard UI.
    
    Args:
        n_points: Number of data points
        time_span_days: Time span in days
    
    Returns:
        Recommended time unit as string: "1min", "5min", "15min", "1h", "6h", "1d"
    
    Heuristics:
        - < 1 day: 5min
        - 1-3 days: 15min
        - 3-7 days: 1h
        - 7-30 days: 6h
        - > 30 days: 1d
    
    Example:
        >>> recommend_time_unit(10000, 7.5)
        '1h'
    """
    if time_span_days < 1:
        return "5min"
    elif time_span_days < 3:
        return "15min"
    elif time_span_days < 7:
        return "1h"
    elif time_span_days < 30:
        return "6h"
    else:
        return "1d"
