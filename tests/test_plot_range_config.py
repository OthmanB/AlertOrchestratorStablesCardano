#!/usr/bin/env python3
"""
Unit tests for Plot Range Control configuration (Phase A)

Tests cover:
- PlotRangeConfig dataclass and resolve() method
- AggregationConfig dataclass and validation
- Config loading from YAML
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.settings import PlotRangeConfig, AggregationConfig
from src.shared.config import DateRange



class TestPlotRangeConfig:
    """Test PlotRangeConfig dataclass"""
    
    def test_default_initialization(self):
        """Test default values"""
        config = PlotRangeConfig()
        assert config.start is None
        assert config.end is None
        assert config.mode == "inherit"
        assert config.relative_duration is None
    
    def test_resolve_inherit_mode(self):
        """Test inherit mode preserves data range"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        config = PlotRangeConfig(mode="inherit")
        result = config.resolve(data_range)
        
        assert result.start == data_range.start
        assert result.end == data_range.end
    
    def test_resolve_custom_mode_with_values(self):
        """Test custom mode with explicit start/end"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        custom_start = datetime(2025, 10, 15, tzinfo=timezone.utc)
        custom_end = datetime(2025, 10, 20, tzinfo=timezone.utc)
        
        config = PlotRangeConfig(
            mode="custom",
            start=custom_start,
            end=custom_end
        )
        result = config.resolve(data_range)
        
        assert result.start == custom_start
        assert result.end == custom_end
    
    def test_resolve_custom_mode_partial_override(self):
        """Test custom mode with only start specified"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        custom_start = datetime(2025, 10, 15, tzinfo=timezone.utc)
        
        config = PlotRangeConfig(
            mode="custom",
            start=custom_start,
            end=None
        )
        result = config.resolve(data_range)
        
        assert result.start == custom_start
        assert result.end == data_range.end  # Falls back to data_range.end
    
    def test_resolve_relative_mode_days(self):
        """Test relative mode with days duration"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        now = datetime(2025, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
        
        config = PlotRangeConfig(
            mode="relative",
            relative_duration="7d"
        )
        result = config.resolve(data_range, now=now)
        
        expected_start = now - timedelta(days=7)
        assert result.start == expected_start
        assert result.end == now
    
    def test_resolve_relative_mode_hours(self):
        """Test relative mode with hours duration"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        now = datetime(2025, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
        
        config = PlotRangeConfig(
            mode="relative",
            relative_duration="48h"
        )
        result = config.resolve(data_range, now=now)
        
        expected_start = now - timedelta(hours=48)
        assert result.start == expected_start
        assert result.end == now
    
    def test_resolve_relative_mode_weeks(self):
        """Test relative mode with weeks duration"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        now = datetime(2025, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
        
        config = PlotRangeConfig(
            mode="relative",
            relative_duration="2w"
        )
        result = config.resolve(data_range, now=now)
        
        expected_start = now - timedelta(weeks=2)
        assert result.start == expected_start
        assert result.end == now
    
    def test_resolve_relative_mode_invalid_format(self):
        """Test relative mode with invalid duration format falls back"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        
        config = PlotRangeConfig(
            mode="relative",
            relative_duration="invalid"
        )
        result = config.resolve(data_range)
        
        # Should fall back to inherit mode
        assert result.start == data_range.start
        assert result.end == data_range.end
    
    def test_resolve_relative_mode_no_duration(self):
        """Test relative mode without duration specified"""
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 1, tzinfo=timezone.utc)
        )
        
        config = PlotRangeConfig(mode="relative")
        result = config.resolve(data_range)
        
        # Should fall back to inherit mode
        assert result.start == data_range.start
        assert result.end == data_range.end
    
    def test_invalid_mode_fallback(self):
        """Test that invalid mode defaults to inherit"""
        config = PlotRangeConfig(mode="invalid_mode")
        # __post_init__ should have corrected it
        assert config.mode == "inherit"


class TestAggregationConfig:
    """Test AggregationConfig dataclass"""
    
    def test_default_initialization(self):
        """Test default values"""
        config = AggregationConfig()
        assert config.enabled is False
        assert config.method == "whiskers"
        assert config.time_unit == "5min"
        assert config.percentiles == [10.0, 25.0, 50.0, 75.0, 90.0]
    
    def test_custom_percentiles(self):
        """Test custom percentiles"""
        config = AggregationConfig(percentiles=[5, 25, 50, 75, 95])
        assert config.percentiles == [5.0, 25.0, 50.0, 75.0, 95.0]
    
    def test_percentiles_sorting(self):
        """Test that percentiles are sorted"""
        config = AggregationConfig(percentiles=[95, 5, 50, 25, 75])
        assert config.percentiles == [5.0, 25.0, 50.0, 75.0, 95.0]
    
    def test_percentiles_out_of_range_filtered(self):
        """Test that invalid percentiles are filtered"""
        config = AggregationConfig(percentiles=[-10, 10, 25, 50, 75, 150])
        # Should filter out -10 and 150
        assert config.percentiles is not None
        assert -10 not in config.percentiles
        assert 150 not in config.percentiles
        assert 10.0 in config.percentiles
        assert 75.0 in config.percentiles
    
    def test_invalid_method_correction(self):
        """Test that invalid method defaults to whiskers"""
        config = AggregationConfig(method="invalid_method")
        assert config.method == "whiskers"
    
    def test_invalid_time_unit_correction(self):
        """Test that invalid time_unit defaults to 5min"""
        config = AggregationConfig(time_unit="invalid_unit")
        assert config.time_unit == "5min"
    
    def test_valid_time_units(self):
        """Test all valid time units"""
        valid_units = ["1min", "5min", "15min", "1h", "6h", "1d"]
        for unit in valid_units:
            config = AggregationConfig(time_unit=unit)
            assert config.time_unit == unit
    
    def test_valid_methods(self):
        """Test valid methods"""
        for method in ["whiskers", "none"]:
            config = AggregationConfig(method=method)
            assert config.method == method


class TestConfigIntegration:
    """Integration tests for config loading"""
    
    def test_config_loading_with_defaults(self):
        """Test that config loads with default plot_range and aggregation"""
        # This would require loading actual YAML config
        # For now, just test dataclass creation
        plot_range = PlotRangeConfig()
        aggregation = AggregationConfig()
        
        assert plot_range.mode == "inherit"
        assert aggregation.enabled is False
    
    def test_plot_range_custom_config(self):
        """Test custom plot_range configuration"""
        plot_range = PlotRangeConfig(
            start=datetime(2025, 11, 1, tzinfo=timezone.utc),
            end=datetime(2025, 11, 15, tzinfo=timezone.utc),
            mode="custom"
        )
        
        data_range = DateRange(
            start=datetime(2025, 10, 1, tzinfo=timezone.utc),
            end=datetime(2025, 12, 1, tzinfo=timezone.utc)
        )
        
        resolved = plot_range.resolve(data_range)
        assert resolved.start is not None
        assert resolved.start.month == 11
        assert resolved.start.day == 1
        assert resolved.end is not None
        assert resolved.end.day == 15
    
    def test_aggregation_enabled_config(self):
        """Test aggregation enabled configuration"""
        agg = AggregationConfig(
            enabled=True,
            method="whiskers",
            time_unit="15min",
            percentiles=[10, 25, 50, 75, 90]
        )
        
        assert agg.enabled is True
        assert agg.method == "whiskers"
        assert agg.time_unit == "15min"
        assert agg.percentiles is not None
        assert len(agg.percentiles) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
