#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utility Functions for Liqwid Client Aggregator
Shared helpers for logging, time handling, and data processing.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Callable, Any, Dict
from .colored_logging import ColoredFormatter
from typing import Optional, Dict, Any
from pathlib import Path


def parse_datetime(value: Any) -> Optional[datetime]:
    """
    Parse datetime from various formats
    
    Args:
        value: String, datetime, or None
        
    Returns:
        Parsed datetime or None
        
    Raises:
        ValueError: If datetime format is invalid
    """
    if value is None:
        return None
    
    if isinstance(value, datetime):
        return value
    
    if isinstance(value, str):
        # Try common ISO formats
        formats = [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        
        raise ValueError(f"Invalid datetime format: {value}. Expected ISO format like '2025-02-01T00:00:00Z'")
    
    raise ValueError(f"Datetime must be string or datetime object, got {type(value)}")


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    Setup logging configuration
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        
    Returns:
        Configured logger
    """
    # Configure root logger
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    handlers = []
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(fmt=log_format, datefmt=date_format))
    handlers.append(console_handler)
    
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # File handler without colors (plain text for files)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(fmt=log_format, datefmt=date_format))
        handlers.append(file_handler)
    
    # Remove existing handlers
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    # Add new handlers
    root.setLevel(getattr(logging, level.upper()))
    for handler in handlers:
        root.addHandler(handler)
    
    return logging.getLogger("LiqwidClient")


def timestamp_to_datetime(timestamp_ms: int) -> datetime:
    """
    Convert millisecond timestamp to UTC datetime
    
    Args:
        timestamp_ms: Timestamp in milliseconds since epoch
        
    Returns:
        UTC datetime object
    """
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)


def datetime_to_timestamp(dt: datetime) -> int:
    """
    Convert datetime to millisecond timestamp
    
    Args:
        dt: Datetime object
        
    Returns:
        Timestamp in milliseconds since epoch
    """
    return int(dt.timestamp() * 1000)


def format_datetime_for_output(dt: datetime, format_type: str = "iso") -> str:
    """
    Format datetime for output files
    
    Args:
        dt: Datetime to format
        format_type: "iso" or "human"
        
    Returns:
        Formatted datetime string
    """
    if format_type == "iso":
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif format_type == "human":
        return dt.strftime("%Y-%m-%d %H:%M")
    else:
        raise ValueError(f"Unknown format_type: {format_type}")


def sanitize_filename_timestamp(dt: datetime) -> str:
    """
    Create filename-safe timestamp string
    
    Args:
        dt: Datetime to format
        
    Returns:
        Filename-safe timestamp string
    """
    return dt.strftime("%Y%m%d_%H%M%S")


def retry_with_backoff(
    func,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Retry function with exponential backoff
    
    Args:
        func: Function to retry
        max_attempts: Maximum number of attempts
        base_delay: Initial delay in seconds
        backoff_factor: Multiplier for delay on each retry
        exceptions: Tuple of exceptions to catch and retry
        
    Returns:
        Function result
        
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_attempts):
        try:
            return func()
        except exceptions as e:
            last_exception = e
            
            if attempt < max_attempts - 1:
                delay = base_delay * (backoff_factor ** attempt)
                logging.getLogger().warning(
                    f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                logging.getLogger().error(f"All {max_attempts} attempts failed")
    
    if last_exception:
        raise last_exception


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert value to float
    
    Args:
        value: Value to convert
        default: Default value if conversion fails
        
    Returns:
        Float value or default
    """
    if value is None:
        return default
    
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def normalize_asset_symbol(symbol: str) -> str:
    """
    Normalize asset symbol for consistency
    
    Args:
        symbol: Asset symbol to normalize
        
    Returns:
        Normalized asset symbol (lowercase, stripped)
    """
    if not symbol:
        return ""
    
    return str(symbol).strip().lower()


def canonicalize_minswap_asset(name: str) -> str:
    """
    Canonicalize asset names for Minswap usage.

    Purpose:
    - Ensure aliases like 'usdc' and 'usdt' map to wrapped variants
      'wanusdc' and 'wanusdt' so that Greptime(minswap) table names and
      aggregator lookups remain consistent with the chosen convention.

    Rules (case-insensitive):
    - 'usdc' -> 'wanusdc'
    - 'usdt' -> 'wanusdt'
    - 'wanusdc' -> 'wanusdc' (idempotent)
    - 'wanusdt' -> 'wanusdt' (idempotent)
    - any other -> normalized lowercase of input

    Args:
        name: Display asset name provided by user/config

    Returns:
        Canonical asset name for Minswap paths
    """
    sym = normalize_asset_symbol(name)
    if sym == "usdc":
        return "wanusdc"
    if sym == "usdt":
        return "wanusdt"
    # Keep existing wrapped names or other symbols as-is (normalized)
    return sym


# ===== Bech32 utilities (minimal, no external deps) =====
# Based on BIP-0173 and BIP-0350 references

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CHARSET_REV = {c: i for i, c in enumerate(_BECH32_CHARSET)}
_BECH32_CONST = 1
_BECH32M_CONST = 0x2bc830a3


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            if (b >> i) & 1:
                chk ^= generator[i]
    return chk


def _bech32_verify_checksum(hrp: str, data: list[int]) -> int | None:
    const = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if const == _BECH32_CONST:
        return _BECH32_CONST
    if const == _BECH32M_CONST:
        return _BECH32M_CONST
    return None


def _bech32_decode(bech: str) -> tuple[str | None, list[int] | None, int | None]:
    if not isinstance(bech, str):
        return None, None, None
    if any(ord(x) < 33 or ord(x) > 126 for x in bech):
        return None, None, None
    # Disallow mixed case
    if any(c.isupper() for c in bech) and any(c.islower() for c in bech):
        return None, None, None
    bech = bech.lower()
    pos = bech.rfind('1')
    if pos < 1 or pos + 7 > len(bech):
        return None, None, None
    hrp = bech[:pos]
    data_part = bech[pos + 1:]
    try:
        data = [_BECH32_CHARSET_REV[c] for c in data_part]
    except KeyError:
        return None, None, None
    spec = _bech32_verify_checksum(hrp, data)
    if spec is None:
        return None, None, None
    return hrp, data[:-6], spec


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool = True) -> list[int] | None:
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def is_valid_cardano_address(addr: str) -> bool:
    """
    Strict Cardano address validator (Bech32/Bech32m):
    - Accept HRP 'addr' or 'addr_test'
    - Verify checksum using Bech32 / Bech32m rules
    - Convert 5-bit groups to bytes to ensure non-empty payload
    """
    if not isinstance(addr, str):
        return False
    s = addr.strip()
    if not s:
        return False
    hrp, data, enc = _bech32_decode(s)
    if hrp is None or data is None or enc is None:
        return False
    if hrp not in ("addr", "addr_test"):
        return False
    decoded = _convertbits(data, 5, 8, False)
    if decoded is None or len(decoded) == 0:
        return False
    return True


def build_date_range_filter(
    start_dt: Optional[datetime],
    end_dt: Optional[datetime]
) -> str:
    """
    Build SQL WHERE clause for date range filtering
    
    Args:
        start_dt: Start datetime (inclusive)
        end_dt: End datetime (inclusive)
        
    Returns:
        SQL WHERE clause or empty string if no filtering needed
    """
    conditions = []
    
    if start_dt:
        start_ms = datetime_to_timestamp(start_dt)
        conditions.append(f"ts >= {start_ms}")
    
    if end_dt:
        end_ms = datetime_to_timestamp(end_dt)
        conditions.append(f"ts <= {end_ms}")
    
    if conditions:
        return f"WHERE {' AND '.join(conditions)}"
    
    return ""


def validate_table_name(table_name: str, prefix: str = "") -> bool:
    """
    Validate table name for security
    
    Args:
        table_name: Table name to validate
        prefix: Expected prefix
        
    Returns:
        True if table name is valid
    """
    if not table_name:
        return False
    
    # Check for SQL injection patterns
    dangerous_chars = [';', '--', '/*', '*/', 'DROP', 'DELETE', 'INSERT', 'UPDATE']
    table_upper = table_name.upper()
    
    for char in dangerous_chars:
        if char in table_upper:
            return False
    
    # Check prefix if provided
    if prefix and not table_name.startswith(prefix):
        return False
    
    return True


class ProgressTracker:
    """Simple progress tracking for long operations"""
    
    def __init__(self, total: int, description: str = "Processing"):
        self.total = total
        self.current = 0
        self.description = description
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def update(self, increment: int = 1) -> None:
        """Update progress and log if significant milestone reached"""
        self.current += increment
        
        # Log progress at 25%, 50%, 75%, 100%
        percentage = (self.current / self.total) * 100
        
        if percentage >= 25 and (self.current - increment) / self.total * 100 < 25:
            self.logger.info(f"{self.description}: 25% complete ({self.current}/{self.total})")
        elif percentage >= 50 and (self.current - increment) / self.total * 100 < 50:
            self.logger.info(f"{self.description}: 50% complete ({self.current}/{self.total})")
        elif percentage >= 75 and (self.current - increment) / self.total * 100 < 75:
            self.logger.info(f"{self.description}: 75% complete ({self.current}/{self.total})")
        elif self.current >= self.total:
            self.logger.info(f"{self.description}: Complete! ({self.current}/{self.total})")
    
    def finish(self) -> None:
        """Mark as complete"""
        self.current = self.total
        self.logger.info(f"{self.description}: Complete! ({self.current}/{self.total})")


def format_number_with_commas(number: float, decimals: int = 2) -> str:
    """
    Format number with thousands separators
    
    Args:
        number: Number to format
        decimals: Number of decimal places
        
    Returns:
        Formatted number string
    """
    return f"{number:,.{decimals}f}"


def calculate_percentage_change(old_value: float, new_value: float) -> Optional[float]:
    """
    Calculate percentage change between two values
    
    Args:
        old_value: Original value
        new_value: New value
        
    Returns:
        Percentage change or None if old_value is zero
    """
    if old_value == 0:
        return None
    
    return ((new_value - old_value) / old_value) * 100


def determine_smart_ylimits(raw_data: list, smoothed_data: Optional[list] = None, 
                           data_type: str = 'percentage') -> tuple[float, float]:
    """
    Determine smart y-axis limits for gains charts to avoid noise domination.
    
    Args:
        raw_data: List of raw gain values
        smoothed_data: List of smoothed gain values (optional)
        data_type: 'percentage' or 'absolute' to determine appropriate caps
        
    Returns:
        Tuple of (y_min, y_max) for setting axis limits
        
    Logic:
    - Primary scaling based on smoothed data if available, otherwise raw data
    - Apply reasonable caps to prevent noise from dominating scale
    - For percentage: cap at [-0.1, 5.0] to handle typical gain ranges
    - For absolute: scale based on data but with outlier protection
    """
    import numpy as np
    
    # Use smoothed data for scaling if available, otherwise raw data
    primary_data = smoothed_data if smoothed_data and len(smoothed_data) > 0 else raw_data
    
    if not primary_data or len(primary_data) == 0:
        # Default fallback
        return (-0.1, 5.0) if data_type == 'percentage' else (-100, 1000)
    
    # Calculate statistics on primary data
    primary_array = np.array(primary_data)
    mean_val = np.mean(primary_array)
    std_val = np.std(primary_array)
    median_val = np.median(primary_array)
    
    # Calculate robust range using smoothed data statistics
    # Use mean ± 3*std, but also consider the median for robustness
    robust_min = min(mean_val - 3 * std_val, median_val - 2 * std_val)
    robust_max = max(mean_val + 3 * std_val, median_val + 2 * std_val)
    
    # Add some padding for visibility
    padding = max(abs(robust_max - robust_min) * 0.1, std_val * 0.5)
    y_min = robust_min - padding
    y_max = robust_max + padding
    
    # Apply caps based on data type
    if data_type == 'percentage':
        # Cap percentage gains to reasonable range
        y_min = max(y_min, -0.1)  # Don't go below -10% per period
        y_max = min(y_max, 5.0)   # Don't go above 500% per period
        
        # Ensure minimum range for visibility
        if y_max - y_min < 0.02:  # Less than 2% range
            center = (y_max + y_min) / 2
            y_min = center - 0.01
            y_max = center + 0.01
            
    else:  # absolute values
        # For absolute values, be more flexible but still cap extreme outliers
        data_range = robust_max - robust_min
        max_reasonable_range = abs(mean_val) * 10  # 10x the mean as max range
        
        if data_range > max_reasonable_range:
            # Scale down if range is too extreme
            center = mean_val
            half_range = max_reasonable_range / 2
            y_min = center - half_range
            y_max = center + half_range
    
    # Ensure y_min < y_max
    if y_min >= y_max:
        y_min = mean_val - abs(mean_val) * 0.1
        y_max = mean_val + abs(mean_val) * 0.1
    
    return (float(y_min), float(y_max))


def determine_time_unit_and_scaling(time_range_hours: float) -> tuple[str, str, float, float]:
    """
    Determine appropriate time unit and scaling factors based on data time range.
    
    Args:
        time_range_hours: Total time range of data in hours
        
    Returns:
        Tuple of (abs_unit, pct_unit, scale_factor, monthly_scale_factor):
        - abs_unit: Unit string for absolute gains (e.g., "USD/day")
        - pct_unit: Unit string for percentage gains (e.g., "%/day") 
        - scale_factor: Factor to scale hourly data to chosen unit
        - monthly_scale_factor: Factor to scale hourly data to monthly for text box
        
    Logic:
    - < 1 day (24h): USD/hour, %/hour
    - 1 day to 1 week (168h): USD/day, %/day  
    - 1 week to 1 month (720h): USD/week, %/week
    - > 1 month: USD/month, %/month
    """
    hours_per_day = 24
    hours_per_week = 24 * 7  # 168
    hours_per_month = 24 * 30  # 720 (approximate)
    
    monthly_scale_factor = hours_per_month  # Always calculate monthly estimate
    
    if time_range_hours < hours_per_day:
        # Less than 1 day: use hourly
        return "USD/hour", "%/hour", 1.0, monthly_scale_factor
    elif time_range_hours < hours_per_week:
        # 1 day to 1 week: use daily
        return "USD/day", "%/day", hours_per_day, monthly_scale_factor
    elif time_range_hours < hours_per_month:
        # 1 week to 1 month: use weekly  
        return "USD/week", "%/week", hours_per_week, monthly_scale_factor
    else:
        # More than 1 month: use monthly
        return "USD/month", "%/month", hours_per_month, monthly_scale_factor


def remove_smooth_curve_outliers(smooth_data: list, percentile: float = 95.0) -> list:
    """
    Remove outliers from smooth curve data based on probability density distribution.
    
    This function filters out edge effects and other outliers in smoothed data by:
    1. Computing the probability density function of the smooth curve values
    2. Rejecting points that exceed the specified percentile (default 99%)
    3. Returning the filtered data with outliers replaced by NaN or interpolated values
    
    Args:
        smooth_data: List of smoothed curve values (e.g., polynomial fit results)
        percentile: Percentile threshold for outlier rejection (default 99.0)
        
    Returns:
        List of filtered smooth data with outliers removed/replaced
        
    Example:
        >>> smooth_values = [0.01, 0.02, 0.015, 8.5, 0.018, 0.02, -5.2, 0.019]  # Contains outliers
        >>> filtered = remove_smooth_curve_outliers(smooth_values, 99.0)
        >>> # Returns filtered data with extreme values (8.5, -5.2) handled
    """
    import numpy as np
    
    # Handle edge cases
    if not smooth_data or len(smooth_data) == 0:
        return smooth_data
    
    # Convert to numpy array for easier processing
    data_array = np.array(smooth_data, dtype=float)
    
    # Handle case where all values are the same or nearly the same
    if np.std(data_array) < 1e-10:
        return smooth_data  # No outliers if no variation
    
    # Calculate percentile thresholds
    lower_threshold = np.percentile(data_array, (100 - percentile) / 2)
    upper_threshold = np.percentile(data_array, percentile + (100 - percentile) / 2)
    
    # Create mask for valid values (within thresholds)
    valid_mask = (data_array >= lower_threshold) & (data_array <= upper_threshold)
    
    # Create filtered array
    filtered_data = data_array.copy()

    # Replace outliers with interpolated values or nearest valid values
    outlier_indices = np.where(~valid_mask)[0]
    
    for idx in outlier_indices:
        # Find nearest valid values for interpolation
        valid_indices = np.where(valid_mask)[0]
        
        if len(valid_indices) == 0:
            # If no valid values, keep original (shouldn't happen with reasonable data)
            continue
            
        # Find closest valid indices
        distances = np.abs(valid_indices - idx)
        closest_idx = valid_indices[np.argmin(distances)]
        
        # Use median of valid values as replacement (robust choice)
        filtered_data[idx] = np.median(data_array[valid_mask])
    
    return filtered_data.tolist()
