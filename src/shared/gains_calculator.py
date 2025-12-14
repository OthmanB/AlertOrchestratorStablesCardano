#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gains Calculator for Liqwid Client Aggregator
Handles derivative calculations, smoothing functions, and gains analysis.
"""

import numpy as np
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from scipy import ndimage
from scipy.ndimage import gaussian_filter1d

from .models import AggregatedRow, GainsRow
from .config import AssetSmoothingConfig, SmoothingMethod


class GainsCalculationError(Exception):
    """Gains calculation-related errors"""
    pass


class GainsCalculator:
    """
    Calculates gains over time with configurable smoothing
    
    Handles:
    - High-precision derivative calculations with edge handling
    - Gaussian and box-car smoothing with time-based windows
    - Conversion between absolute and percentage gains
    - Adaptive time step handling for irregular data
    """
    
    def __init__(self, smoothing_config: AssetSmoothingConfig):
        """
        Initialize gains calculator
        
        Args:
            smoothing_config: Asset-specific smoothing configuration settings
        """
        self.smoothing_config = smoothing_config
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def calculate_gains(self, rows: List[AggregatedRow]) -> List[GainsRow]:
        """
        Calculate gains over time with smoothing
        
        Args:
            rows: Aggregated data rows (must be sorted by timestamp)
            
        Returns:
            List of gains rows with raw and smoothed gains
            
        Raises:
            GainsCalculationError: If calculation fails
        """
        if len(rows) < 2:
            raise GainsCalculationError("Need at least 2 data points for gains calculation")
        
        try:
            self.logger.info(f"Calculating gains for {len(rows)} data points")
            
            # Extract time series data
            timestamps = [row.timestamp for row in rows]
            values = [row.total for row in rows]
            
            # Calculate time steps (hours)
            time_steps = self._calculate_time_steps(timestamps)
            
            # Apply smoothing to values BEFORE taking derivatives (for noise reduction)
            smoothed_values = None
            if self.smoothing_config.default.window_type != "none":
                smoothed_values = self._apply_smoothing(values, time_steps, timestamps)
            
            # Calculate derivatives from raw values
            raw_absolute_gains = self._calculate_derivatives(values, time_steps)
            raw_percentage_gains = self._calculate_percentage_derivatives(values, raw_absolute_gains)
            
            # Calculate derivatives from smoothed values (if smoothing is enabled)
            smoothed_absolute_gains = None
            smoothed_percentage_gains = None
            if smoothed_values is not None:
                smoothed_absolute_gains = self._calculate_derivatives(smoothed_values, time_steps)
                smoothed_percentage_gains = self._calculate_percentage_derivatives(smoothed_values, smoothed_absolute_gains)
            
            # Create gains rows
            gains_rows = []
            for i in range(len(rows)):
                gains_row = GainsRow(
                    timestamp=timestamps[i],
                    raw_absolute_gain=raw_absolute_gains[i],
                    raw_percentage_gain=raw_percentage_gains[i],
                    smoothed_absolute_gain=smoothed_absolute_gains[i] if smoothed_absolute_gains is not None else None,
                    smoothed_percentage_gain=smoothed_percentage_gains[i] if smoothed_percentage_gains is not None else None,
                    reference_value=values[i]
                )
                gains_rows.append(gains_row)
            
            self.logger.info(f"Calculated gains for {len(gains_rows)} points")
            return gains_rows
            
        except Exception as e:
            error_msg = f"Failed to calculate gains: {e}"
            self.logger.error(error_msg)
            raise GainsCalculationError(error_msg)
    
    def calculate_gains_for_asset(
        self, 
        rows: List[AggregatedRow], 
        asset_symbol: str
    ) -> List[GainsRow]:
        """
        Calculate gains using asset-specific smoothing configuration
        
        Args:
            rows: Aggregated data rows (must be sorted by timestamp)
            asset_symbol: Asset symbol to get specific configuration for
            
        Returns:
            List of gains rows with raw and smoothed gains using asset-specific config
            
        Raises:
            GainsCalculationError: If calculation fails
        """
        if len(rows) < 2:
            raise GainsCalculationError("Need at least 2 data points for gains calculation")
        
        try:
            asset_config = self.smoothing_config.get_config_for_asset(asset_symbol)
            self.logger.info(f"Calculating gains for {asset_symbol.upper()} with {asset_config.window_type} smoothing")
            
            # Extract time series data
            timestamps = [row.timestamp for row in rows]
            values = [row.total for row in rows]
            
            # Calculate time steps (hours)
            time_steps = self._calculate_time_steps(timestamps)
            
            # Apply asset-specific smoothing to values BEFORE taking derivatives
            smoothed_values = None
            if asset_config.window_type != "none":
                smoothed_values = self._apply_smoothing_by_method(values, time_steps, timestamps, asset_config)
            
            # Calculate derivatives from raw values
            raw_absolute_gains = self._calculate_derivatives(values, time_steps)
            raw_percentage_gains = self._calculate_percentage_derivatives(values, raw_absolute_gains)
            
            # Calculate derivatives from smoothed values (if smoothing is enabled)
            smoothed_absolute_gains = None
            smoothed_percentage_gains = None
            if smoothed_values is not None:
                smoothed_absolute_gains = self._calculate_derivatives(smoothed_values, time_steps)
                smoothed_percentage_gains = self._calculate_percentage_derivatives(smoothed_values, smoothed_absolute_gains)
            
            # Create gains rows
            gains_rows = []
            for i in range(len(rows)):
                gains_row = GainsRow(
                    timestamp=timestamps[i],
                    raw_absolute_gain=raw_absolute_gains[i],
                    raw_percentage_gain=raw_percentage_gains[i],
                    smoothed_absolute_gain=smoothed_absolute_gains[i] if smoothed_absolute_gains is not None else None,
                    smoothed_percentage_gain=smoothed_percentage_gains[i] if smoothed_percentage_gains is not None else None,
                    reference_value=values[i]
                )
                gains_rows.append(gains_row)
            
            self.logger.info(f"Calculated gains for {asset_symbol.upper()}: {len(gains_rows)} points")
            return gains_rows
            
        except Exception as e:
            error_msg = f"Failed to calculate gains for {asset_symbol}: {e}"
            self.logger.error(error_msg)
            raise GainsCalculationError(error_msg)
    
    def _calculate_time_steps(self, timestamps: List[datetime]) -> List[float]:
        """
        Calculate time steps between consecutive timestamps in hours
        
        Args:
            timestamps: List of datetime objects
            
        Returns:
            List of time steps in hours (same length as timestamps)
        """
        if len(timestamps) < 2:
            return [1.0]  # Default 1 hour for single point
        
        time_steps = []
        
        # First point: use forward difference
        dt = (timestamps[1] - timestamps[0]).total_seconds() / 3600.0
        time_steps.append(dt)
        
        # Middle points: use centered difference
        for i in range(1, len(timestamps) - 1):
            dt = (timestamps[i+1] - timestamps[i-1]).total_seconds() / 7200.0  # /2 for centered diff
            time_steps.append(dt)
        
        # Last point: use backward difference  
        dt = (timestamps[-1] - timestamps[-2]).total_seconds() / 3600.0
        time_steps.append(dt)
        
        return time_steps
    
    def _calculate_derivatives(self, values: List[float], time_steps: List[float]) -> List[float]:
        """
        Calculate high-precision derivatives with adaptive order based on available points
        Uses 7-point > 5-point > 3-point > 2-point difference schemes
        
        Args:
            values: Values to differentiate
            time_steps: Time steps in hours
            
        Returns:
            List of derivatives (USD per hour)
        """
        if len(values) < 2:
            return [0.0]
        
        n = len(values)
        derivatives = []
        
        for i in range(n):
            # Determine how many points we can use around point i
            left_points = i
            right_points = n - 1 - i
            
            # Choose highest order derivative possible
            if left_points >= 3 and right_points >= 3:
                # 7-point centered difference
                h = np.mean(time_steps[max(0, i-3):min(n, i+4)])
                if h > 0:
                    dv = (-values[i-3] + 9*values[i-2] - 45*values[i-1] + 45*values[i+1] - 9*values[i+2] + values[i+3]) / 60
                    derivative = dv / h
                else:
                    derivative = 0.0
                    
            elif left_points >= 2 and right_points >= 2:
                # 5-point centered difference  
                h = np.mean(time_steps[max(0, i-2):min(n, i+3)])
                if h > 0:
                    dv = (-values[i-2] + 8*values[i-1] - 8*values[i+1] + values[i+2]) / 12
                    derivative = dv / h
                else:
                    derivative = 0.0
                    
            elif left_points >= 1 and right_points >= 1:
                # 3-point centered difference
                h = time_steps[i]
                if h > 0:
                    dv = values[i+1] - values[i-1]
                    derivative = dv / (2 * h)
                else:
                    derivative = 0.0
                    
            elif right_points >= 1:
                # 2-point forward difference
                h = time_steps[i]
                if h > 0:
                    dv = values[i+1] - values[i]
                    derivative = dv / h
                else:
                    derivative = 0.0
                    
            elif left_points >= 1:
                # 2-point backward difference
                h = time_steps[i]
                if h > 0:
                    dv = values[i] - values[i-1]
                    derivative = dv / h
                else:
                    derivative = 0.0
                    
            else:
                # Single point - no derivative possible
                derivative = 0.0
                
            derivatives.append(derivative)
        
        return derivatives
    
    def _calculate_percentage_derivatives(self, values: List[float], absolute_gains: List[float]) -> List[float]:
        """
        Calculate percentage derivatives from absolute gains
        
        Args:
            values: Reference values for percentage calculation
            absolute_gains: Absolute gains in USD per hour
            
        Returns:
            List of percentage gains (% per hour)
        """
        percentage_gains = []
        
        for value, abs_gain in zip(values, absolute_gains):
            if value > 1e-6:  # Avoid division by very small numbers
                pct_gain = (abs_gain / value) * 100.0
            else:
                pct_gain = 0.0
            percentage_gains.append(pct_gain)
        
        return percentage_gains
    
    def _apply_smoothing_by_method(
        self,
        values: List[float],
        time_steps: List[float],
        timestamps: List[datetime],
        config: SmoothingMethod
    ) -> List[float]:
        """
        Apply smoothing based on method type in config
        
        Args:
            values: Values to smooth
            time_steps: Time steps in hours
            timestamps: Corresponding timestamps
            config: Smoothing method configuration
            
        Returns:
            Smoothed values
        """
        if config.window_type == "polynomial":
            return self._apply_polynomial_smoothing(values, timestamps, config.polynomial_order)
        elif config.window_type == "gaussian":
            return self._apply_gaussian_smoothing_with_config(values, time_steps, timestamps, config)
        elif config.window_type == "boxcar":
            return self._apply_boxcar_smoothing_with_config(values, time_steps, timestamps, config)
        else:  # "none"
            return values.copy()
    
    def _apply_polynomial_smoothing(
        self,
        values: List[float],
        timestamps: List[datetime],
        order: int
    ) -> List[float]:
        """
        Apply polynomial fitting to extract trend
        
        Uses numpy.polyfit for robust polynomial regression
        Returns smoothed values following polynomial trend
        
        Args:
            values: Values to fit
            timestamps: Corresponding timestamps
            order: Polynomial order (1=linear, 2=quadratic, etc.)
            
        Returns:
            Smoothed values from polynomial fit
        """
        if len(values) <= order:
            self.logger.warning(f"Insufficient data points ({len(values)}) for polynomial order {order}, returning original")
            return values.copy()
        
        try:
            # Convert timestamps to numerical values (hours since start)
            start_time = timestamps[0]
            time_numeric = [(ts - start_time).total_seconds() / 3600.0 for ts in timestamps]
            
            # Apply polynomial fitting
            coefficients = np.polyfit(time_numeric, values, order)
            polynomial = np.poly1d(coefficients)
            
            # Evaluate polynomial at original timestamps
            fitted_values = polynomial(time_numeric)
            
            self.logger.debug(f"Applied polynomial fitting (order {order}) to {len(values)} points")
            return fitted_values.tolist()
            
        except Exception as e:
            self.logger.warning(f"Polynomial fitting failed: {e}, returning original values")
            return values.copy()

    def _apply_smoothing(
        self, 
        data: List[float], 
        time_steps: List[float], 
        timestamps: List[datetime]
    ) -> List[float]:
        """
        Apply smoothing based on default configuration (backward compatibility)
        
        Args:
            data: Data to smooth
            time_steps: Time steps in hours
            timestamps: Corresponding timestamps
            
        Returns:
            Smoothed data
        """
        # Use default configuration for backward compatibility
        default_config = self.smoothing_config.default
        return self._apply_smoothing_by_method(data, time_steps, timestamps, default_config)
    
    def _apply_gaussian_smoothing_with_config(
        self,
        data: List[float],
        time_steps: List[float],
        timestamps: List[datetime],
        config: SmoothingMethod
    ) -> List[float]:
        """
        Apply Gaussian smoothing with specific config
        
        Args:
            data: Data to smooth
            time_steps: Time steps in hours
            timestamps: Corresponding timestamps
            config: Smoothing configuration
            
        Returns:
            Gaussian smoothed data
        """
        data_array = np.array(data)
        
        # Calculate effective sampling rate (average time step)
        avg_time_step = np.mean(time_steps)
        
        # Convert window size to data points
        window_points = config.window_size_hours / avg_time_step
        # Limit window size for stability
        window_points = min(window_points, len(data) / 2.0)
        
        # Calculate sigma in data points
        sigma = window_points * config.gaussian_std
        
        # Apply Gaussian filter
        if sigma > 0.1:  # Only smooth if sigma is significant
            smoothed = gaussian_filter1d(data_array, sigma=sigma, mode='nearest')
            return smoothed.tolist()
        else:
            return data
    
    def _apply_boxcar_smoothing_with_config(
        self,
        data: List[float],
        time_steps: List[float],
        timestamps: List[datetime],
        config: SmoothingMethod
    ) -> List[float]:
        """
        Apply box-car smoothing with specific config
        
        Args:
            data: Data to smooth
            time_steps: Time steps in hours
            timestamps: Corresponding timestamps
            config: Smoothing configuration
            
        Returns:
            Box-car smoothed data
        """
        data_array = np.array(data)
        
        # Calculate effective sampling rate
        avg_time_step = np.mean(time_steps)
        
        # Convert window size to data points
        window_points = int(config.window_size_hours / avg_time_step)
        # Limit window size to data length for stability
        window_points = min(window_points, len(data) // 2)
        window_points = max(1, window_points)  # At least 1 point
        
        if window_points > 1:
            # Create uniform kernel
            kernel = np.ones(window_points) / window_points
            
            # Apply convolution with padding
            smoothed = np.convolve(data_array, kernel, mode='same')
            return smoothed.tolist()
        else:
            return data
    
    def _apply_gaussian_smoothing(
        self, 
        data: np.ndarray, 
        time_steps: List[float], 
        timestamps: List[datetime]
    ) -> List[float]:
        """
        Apply Gaussian smoothing with time-based window
        
        Args:
            data: Data array to smooth
            time_steps: Time steps in hours
            timestamps: Corresponding timestamps
            
        Returns:
            Gaussian smoothed data
        """
        # Calculate effective sampling rate (average time step)
        avg_time_step = np.mean(time_steps)
        
        # Convert window size to data points
        window_points = self.smoothing_config.default.window_size_hours / avg_time_step
        
        # Calculate sigma in data points
        sigma = window_points * self.smoothing_config.default.gaussian_std
        
        # Apply Gaussian filter
        if sigma > 0.1:  # Only smooth if sigma is significant
            smoothed = gaussian_filter1d(data, sigma=sigma, mode='nearest')
            return smoothed.tolist()
        else:
            return data.tolist()
    
    def _apply_boxcar_smoothing(
        self, 
        data: np.ndarray, 
        time_steps: List[float], 
        timestamps: List[datetime]
    ) -> List[float]:
        """
        Apply box-car (uniform) smoothing with time-based window
        
        Args:
            data: Data array to smooth
            time_steps: Time steps in hours  
            timestamps: Corresponding timestamps
            
        Returns:
            Box-car smoothed data
        """
        # Calculate effective sampling rate
        avg_time_step = np.mean(time_steps)
        
        # Convert window size to data points
        window_points = int(self.smoothing_config.default.window_size_hours / avg_time_step)
        # Limit window size to data length for stability
        window_points = min(window_points, len(data) // 2)
        window_points = max(1, window_points)  # At least 1 point
        
        # Create uniform kernel
        kernel = np.ones(window_points) / window_points
        
        # Apply convolution with padding
        if window_points > 1:
            smoothed = ndimage.convolve1d(data, kernel, mode='nearest')
            return smoothed.tolist()
        else:
            return data.tolist()
    
    def calculate_summary_stats(self, gains_rows: List[GainsRow]) -> dict:
        """
        Calculate summary statistics for gains
        
        Args:
            gains_rows: List of gains data
            
        Returns:
            Dictionary with summary statistics
        """
        if not gains_rows:
            return {}
        
        # Extract data for analysis
        raw_abs = [row.raw_absolute_gain for row in gains_rows]
        raw_pct = [row.raw_percentage_gain for row in gains_rows]
        
        stats = {
            'raw_absolute': {
                'mean': np.mean(raw_abs),
                'std': np.std(raw_abs),
                'min': np.min(raw_abs),
                'max': np.max(raw_abs),
                'median': np.median(raw_abs)
            },
            'raw_percentage': {
                'mean': np.mean(raw_pct),
                'std': np.std(raw_pct),
                'min': np.min(raw_pct),
                'max': np.max(raw_pct),
                'median': np.median(raw_pct)
            }
        }
        
        # Add smoothed statistics if available
        if gains_rows[0].smoothed_absolute_gain is not None:
            smoothed_abs = [row.smoothed_absolute_gain for row in gains_rows if row.smoothed_absolute_gain is not None]
            smoothed_pct = [row.smoothed_percentage_gain for row in gains_rows if row.smoothed_percentage_gain is not None]
            
            if smoothed_abs:  # Only calculate if we have valid data
                stats['smoothed_absolute'] = {
                    'mean': np.mean(smoothed_abs),
                    'std': np.std(smoothed_abs),
                    'min': np.min(smoothed_abs),
                    'max': np.max(smoothed_abs),
                    'median': np.median(smoothed_abs)
                }
            
            if smoothed_pct:  # Only calculate if we have valid data
                stats['smoothed_percentage'] = {
                    'mean': np.mean(smoothed_pct),
                    'std': np.std(smoothed_pct),
                    'min': np.min(smoothed_pct),
                    'max': np.max(smoothed_pct),
                    'median': np.median(smoothed_pct)
                }
        
        return stats