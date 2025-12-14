#!/usr/bin/env python3
"""
Unit tests for aggregation module (Phase D)

Tests cover:
- aggregate_timeseries() with various time units
- Edge cases (0, 1, many points per bin)
- Percentile calculation accuracy
- Error handling
"""
import pytest
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.aggregation import aggregate_timeseries, should_aggregate, recommend_time_unit


class TestAggregateTimeseries:
    """Test aggregate_timeseries function"""
    
    def test_empty_input(self):
        """Test handling of empty input arrays"""
        timestamps = np.array([])
        values = np.array([])
        
        bin_centers, stats = aggregate_timeseries(timestamps, values)
        
        assert len(bin_centers) == 0
        assert len(stats) == 0
    
    def test_length_mismatch(self):
        """Test that length mismatch raises ValueError"""
        timestamps = np.array([datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)])
        values = np.array([100.0, 200.0])  # Mismatched length
        
        with pytest.raises(ValueError, match="Length mismatch"):
            aggregate_timeseries(timestamps, values)
    
    def test_single_point(self):
        """Test aggregation with a single data point"""
        timestamps = np.array([datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)])
        values = np.array([100.0])
        
        bin_centers, stats = aggregate_timeseries(timestamps, values, "5min")
        
        assert len(bin_centers) == 1
        assert stats['p50'][0] == 100.0  # Median equals the single value
        assert stats['count'][0] == 1
    
    def test_5min_aggregation(self):
        """Test 5-minute aggregation with regular data"""
        # Create 30 points over 2 hours (every 4 minutes)
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(minutes=i*4) for i in range(30)])
        values = np.array([100.0 + i for i in range(30)])
        
        bin_centers, stats = aggregate_timeseries(timestamps, values, "5min")
        
        # Should have bins (some may have 1 point, some 2)
        assert len(bin_centers) > 0
        assert 'p50' in stats
        assert 'count' in stats
        assert len(stats['p50']) == len(bin_centers)
    
    def test_1h_aggregation(self):
        """Test 1-hour aggregation with dense data"""
        # Create 120 points over 4 hours (every 2 minutes)
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(minutes=i*2) for i in range(120)])
        values = np.array([100.0 + np.sin(i * 0.1) * 10 for i in range(120)])
        
        bin_centers, stats = aggregate_timeseries(timestamps, values, "1h")
        
        # Should have ~4 bins
        assert len(bin_centers) >= 3
        assert len(bin_centers) <= 5
        # Each bin should have ~30 points
        assert all(count > 20 for count in stats['count'])
    
    def test_percentile_calculation(self):
        """Test that percentiles are calculated correctly"""
        # Create controlled data: 10 points in one bin with known values
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(seconds=i) for i in range(10)])
        values = np.array([float(i) for i in range(10)])  # 0, 1, 2, ..., 9
        
        bin_centers, stats = aggregate_timeseries(
            timestamps, values, "1h",
            percentiles=[0, 25, 50, 75, 100]
        )
        
        assert len(bin_centers) == 1
        # Check percentiles
        assert stats['p0'][0] == 0.0   # Min
        assert stats['p50'][0] == 4.5  # Median
        assert stats['p100'][0] == 9.0  # Max
    
    def test_multiple_bins_with_varying_density(self):
        """Test aggregation with bins having different point counts"""
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        
        # First 5 minutes: 10 points
        timestamps_1 = [base_time + timedelta(seconds=i*30) for i in range(10)]
        values_1 = [100.0] * 10
        
        # Skip 5 minutes (empty bin)
        
        # Next 5 minutes: 5 points
        timestamps_2 = [base_time + timedelta(minutes=10) + timedelta(seconds=i*60) for i in range(5)]
        values_2 = [200.0] * 5
        
        timestamps = np.array(timestamps_1 + timestamps_2)
        values = np.array(values_1 + values_2)
        
        bin_centers, stats = aggregate_timeseries(timestamps, values, "5min")
        
        # Should have 2 bins (empty bin skipped)
        assert len(bin_centers) == 2
        assert stats['count'][0] == 10
        assert stats['count'][1] == 5
        assert stats['p50'][0] == 100.0
        assert stats['p50'][1] == 200.0
    
    def test_unsorted_input(self):
        """Test that function handles unsorted timestamps"""
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([
            base_time + timedelta(minutes=5),
            base_time + timedelta(minutes=1),
            base_time + timedelta(minutes=3),
        ])
        values = np.array([3.0, 1.0, 2.0])
        
        bin_centers, stats = aggregate_timeseries(timestamps, values, "5min")
        
        # Should handle sorting internally
        assert len(bin_centers) == 1
        assert stats['count'][0] == 3
    
    def test_all_time_units(self):
        """Test all supported time units"""
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(hours=i) for i in range(48)])
        values = np.array([100.0 + i for i in range(48)])
        
        time_units = ["1min", "5min", "15min", "1h", "6h", "1d"]
        
        for time_unit in time_units:
            bin_centers, stats = aggregate_timeseries(timestamps, values, time_unit)
            assert len(bin_centers) > 0, f"Failed for time_unit={time_unit}"
            assert len(stats['p50']) == len(bin_centers)
    
    def test_invalid_time_unit(self):
        """Test that invalid time_unit falls back to default"""
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(minutes=i) for i in range(20)])
        values = np.array([100.0] * 20)
        
        # Should fall back to "5min" without crashing
        bin_centers, stats = aggregate_timeseries(timestamps, values, "invalid_unit")
        
        assert len(bin_centers) > 0
    
    def test_custom_percentiles(self):
        """Test custom percentile list"""
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(seconds=i) for i in range(100)])
        values = np.array([float(i) for i in range(100)])
        
        bin_centers, stats = aggregate_timeseries(
            timestamps, values, "1h",
            percentiles=[5, 95]
        )
        
        assert 'p5' in stats
        assert 'p95' in stats
        assert 'p50' not in stats  # Only requested percentiles
    
    def test_large_dataset(self):
        """Test performance with large dataset (10k points)"""
        base_time = datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)
        timestamps = np.array([base_time + timedelta(seconds=i) for i in range(10000)])
        values = np.random.normal(100, 10, 10000)
        
        bin_centers, stats = aggregate_timeseries(timestamps, values, "5min")
        
        # Should aggregate significantly
        assert len(bin_centers) < 1000  # Should be much fewer than 10k
        assert len(bin_centers) > 30    # But more than 30 5-min bins (~2.7 hours)
        # Verify all bins have data
        assert all(count > 0 for count in stats['count'])


class TestShouldAggregate:
    """Test should_aggregate function"""
    
    def test_below_threshold(self):
        """Test that aggregation is not recommended below threshold"""
        assert should_aggregate(500, 1.0, threshold_points=1000) is False
    
    def test_above_threshold(self):
        """Test that aggregation is recommended above threshold"""
        assert should_aggregate(5000, 7.0, threshold_points=1000) is True
    
    def test_exact_threshold(self):
        """Test boundary condition at threshold"""
        assert should_aggregate(1000, 7.0, threshold_points=1000) is False
        assert should_aggregate(1001, 7.0, threshold_points=1000) is True
    
    def test_custom_threshold(self):
        """Test with custom threshold"""
        assert should_aggregate(500, 1.0, threshold_points=100) is True
        assert should_aggregate(50, 1.0, threshold_points=100) is False


class TestRecommendTimeUnit:
    """Test recommend_time_unit function"""
    
    def test_short_span_less_than_1_day(self):
        """Test recommendation for < 1 day"""
        assert recommend_time_unit(1000, 0.5) == "5min"
    
    def test_1_to_3_days(self):
        """Test recommendation for 1-3 days"""
        assert recommend_time_unit(5000, 2.0) == "15min"
    
    def test_3_to_7_days(self):
        """Test recommendation for 3-7 days"""
        assert recommend_time_unit(10000, 5.0) == "1h"
    
    def test_7_to_30_days(self):
        """Test recommendation for 7-30 days"""
        assert recommend_time_unit(20000, 14.0) == "6h"
    
    def test_long_span_over_30_days(self):
        """Test recommendation for > 30 days"""
        assert recommend_time_unit(50000, 45.0) == "1d"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
