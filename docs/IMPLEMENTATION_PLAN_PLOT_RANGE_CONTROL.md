# Implementation Plan: Dashboard Plot Range Control & Data Aggregation

**Branch**: `feature/dashboard-plot-range-control`  
**Status**: Planning Phase (READ-ONLY Analysis Complete)  
**Date**: 2025-11-22

---

## Executive Summary

This document outlines the implementation plan for two major enhancements to the Alert Orchestrator dashboard:

1. **Dynamic Plot Range Control**: Decouple visualization range from data sync range, allowing users to adjust visible time windows without re-downloading data
2. **Performance Optimization via Aggregation**: Implement time-based aggregation with statistical whiskers to handle large datasets efficiently

---

## Part 1: Dynamic Plot Range Control

### 1.1 Current Architecture Analysis

#### Data Flow (Current State)
```
orchestrator_config.yaml
  └─ data.date_range (start, end)
      └─ ClientConfig.date_range: DateRange
          ├─ Used by: GreptimeReader queries
          ├─ Used by: Dashboard plot generation
          └─ Fixed window for both sync AND visualization
```

**Key Files:**
- `src/shared/config.py`: `DateRange` dataclass (lines 38-48)
- `src/core/settings.py`: Config loading (lines 610-650, analysis section)
- `src/core/exporter.py`: Dashboard rendering (`_build_chart_b64`, lines 1375-1900)
- `src/core/diagnostics.py`: Plot generation (`plot_residual_composite`, lines 45-439)
- `src/core/io_adapters.py`: Data fetching functions
- `src/core/alert_logic.py`: Decision logic and gains calculation

#### Current Reference Point Logic
**File**: `src/core/reference_state.py` (lines 1-101)
**Config**: `analysis.decision.reference`
- `keyword`: "alert_driven" - Last withdrawal transaction with keyword in notes
- `fallback`: "data_range" - Falls back to `data.date_range.start` if no keyword found

**Problem**: The fallback is tied to data sync range, not visualization range.

---

### 1.2 Required Changes

#### 1.2.1 Configuration Schema Updates

**File**: `alert_orchestrator/config/orchestrator_config.yaml`

**New Section**:
```yaml
visualization:
  diagnostics:
    enabled: false
    dir: "output/plots"
    hist_samples_per_bin: 10
    include_sigma_band: false
    include_k_sigma_band: false
    lookback_hours_override: null
    # NEW: Plot-specific range control
    plot_range:
      start: null              # ISO datetime or null (inherits from data.date_range)
      end: null                # ISO datetime or null (inherits from data.date_range)
      mode: "inherit"          # "inherit" | "custom" | "relative"
      relative_duration: null  # e.g., "7d", "30d" (when mode=relative, counted back from now)
```

**Modified Section**:
```yaml
analysis:
  decision:
    reference:
      keyword: "alert_driven"
      fallback: "data_range"         # EXISTING: falls back to data sync range
      plot_fallback: "plot_range"    # NEW: for gains calculation on dashboard
```

#### 1.2.2 Settings Dataclass Extensions

**File**: `src/core/settings.py`

**New Dataclasses**:
```python
@dataclass
class PlotRangeConfig:
    """Visualization-specific date range (decoupled from sync range)"""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    mode: str = "inherit"  # "inherit" | "custom" | "relative"
    relative_duration: Optional[str] = None  # e.g., "7d", "30d"

    def resolve(self, data_range: DateRange, now: Optional[datetime] = None) -> DateRange:
        """
        Resolve plot range based on mode:
        - inherit: Use data_range as-is
        - custom: Use start/end if set, else data_range
        - relative: Calculate from now - relative_duration to now
        """
        ...

@dataclass
class DiagnosticsConfig:
    # ... existing fields ...
    plot_range: Optional[PlotRangeConfig] = None
```

**Modified Loader** (lines 810-850):
```python
# In _load_v2_schema():
viz = raw.get("visualization", {}) or {}
diag_raw = (viz.get("diagnostics", {}) or {})
plot_range_raw = diag_raw.get("plot_range", {}) or {}
plot_range = PlotRangeConfig(
    start=_parse_datetime(plot_range_raw.get("start")),
    end=_parse_datetime(plot_range_raw.get("end")),
    mode=str(plot_range_raw.get("mode", "inherit")).strip().lower(),
    relative_duration=plot_range_raw.get("relative_duration"),
)
diagnostics = DiagnosticsConfig(
    # ... existing fields ...
    plot_range=plot_range,
)
```

#### 1.2.3 Dashboard UI Controls

**File**: `src/core/exporter.py` (lines 700-950, HTML rendering)

**New HTML Controls** (after "Model Gains" section):
```javascript
<div class="plot-controls" style="margin-top: 1rem; padding: 10px; background: #f8f9fa; border: 1px solid #ddd;">
  <strong>Plot Range:</strong><br/>
  <label>Start: <input type="datetime-local" id="plotStart" value="..." /></label>
  <label>End: <input type="datetime-local" id="plotEnd" value="..." /></label>
  <button onclick="updatePlotRange()">Update Range</button>
  <button onclick="resetPlotRange()">Reset to Data Range</button>
  <span id="plotRangeStatus"></span>
  
  <br/><br/>
  <strong>Gains Reference:</strong><br/>
  <input type="radio" id="refAlert" name="gainsRef" value="alert_driven" checked />
  <label for="refAlert">Last Alert-Driven Transaction</label>
  <input type="radio" id="refPlot" name="gainsRef" value="plot_range" />
  <label for="refPlot">Start of Plot Range</label>
</div>

<script>
async function updatePlotRange() {
  const start = document.getElementById('plotStart').value;
  const end = document.getElementById('plotEnd').value;
  const ref = document.querySelector('input[name="gainsRef"]:checked').value;
  
  // Send to API to update session/config
  const res = await fetch('/api/update-plot-range', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      asset: getCurrentAsset(),
      start: start + 'Z',
      end: end + 'Z',
      gains_reference: ref
    })
  });
  
  if (res.ok) {
    window.location.reload(); // Refresh with new range
  }
}
</script>
```

**New API Endpoint**:
```python
# POST /api/update-plot-range
def do_POST(self_inner):
    if path == "/api/update-plot-range":
        # Parse request body
        content_length = int(self_inner.headers.get('Content-Length', 0))
        body = json.loads(self_inner.rfile.read(content_length).decode())
        
        # Update session-based plot range (stored in MetricsExporter state)
        asset = body.get('asset')
        start = body.get('start')
        end = body.get('end')
        gains_ref = body.get('gains_reference', 'alert_driven')
        
        outer_self._plot_range_overrides[asset] = {
            'start': _parse_datetime(start),
            'end': _parse_datetime(end),
            'gains_reference': gains_ref
        }
        
        # Check if requested range exceeds data range
        data_range = outer_self.settings.client.date_range
        if (start and data_range.start and _parse_datetime(start) < data_range.start) or \
           (end and data_range.end and _parse_datetime(end) > data_range.end):
            # Trigger background data refresh
            outer_self._schedule_data_refresh(asset, start, end)
        
        # Response
        data = json.dumps({"success": True}).encode()
        self_inner.send_response(200)
        self_inner.send_header("Content-Type", "application/json")
        self_inner.end_headers()
        self_inner.wfile.write(data)
```

#### 1.2.4 Data Refresh Logic

**New Method** in `MetricsExporter`:
```python
def _schedule_data_refresh(self, asset: str, requested_start: str, requested_end: str):
    """
    Background task to expand data.date_range and re-sync if plot range exceeds it.
    
    Strategy:
    1. Update ClientConfig.date_range to encompass requested range
    2. Trigger transaction sync for new range
    3. Update Greptime queries to fetch expanded data
    4. Cache results for dashboard use
    """
    import threading
    
    def refresh_task():
        try:
            log.info(f"Expanding data range for {asset}: {requested_start} to {requested_end}")
            
            # Update config (thread-safe copy)
            expanded_range = DateRange(
                start=min(self.settings.client.date_range.start, _parse_datetime(requested_start)),
                end=max(self.settings.client.date_range.end or datetime.now(timezone.utc), _parse_datetime(requested_end))
            )
            
            # Sync transactions for expanded range
            syncer = TransactionSyncer(
                settings=self.settings,
                start_date=expanded_range.start,
                end_date=expanded_range.end
            )
            syncer.sync_all_assets()
            
            # Update internal state
            self._expanded_data_ranges[asset] = expanded_range
            
            log.info(f"Data refresh complete for {asset}")
        except Exception as e:
            log.error(f"Data refresh failed for {asset}: {e}")
    
    thread = threading.Thread(target=refresh_task, daemon=True)
    thread.start()
```

#### 1.2.5 Plot Generation Updates

**File**: `src/core/exporter.py` (`_build_chart_b64` method, lines 1375-1900)

**Modified Logic**:
```python
def _build_chart_b64(self, asset: str, view: str, source: str) -> tuple[...]:
    # ... existing setup ...
    
    # Determine effective plot range
    plot_override = self._plot_range_overrides.get(asset)
    if plot_override:
        plot_range = DateRange(
            start=plot_override['start'],
            end=plot_override['end']
        )
        gains_ref_mode = plot_override['gains_reference']
    else:
        # Use config default
        dbg = self.settings.orchestrator.diagnostics
        plot_cfg = getattr(dbg, 'plot_range', None)
        if plot_cfg:
            plot_range = plot_cfg.resolve(cfg.date_range)
        else:
            plot_range = cfg.date_range
        gains_ref_mode = 'alert_driven'  # default
    
    # Fetch data using EXPANDED data range (if available)
    data_range_effective = self._expanded_data_ranges.get(asset, cfg.date_range)
    
    # ... fetch data using data_range_effective ...
    
    # FILTER data to plot_range AFTER fetching
    x_time_use_full = np.array(...)  # Full data
    cp_vals_use_full = np.array(...)
    
    # Apply plot range filter
    mask = (x_time_use_full >= plot_range.start) & (x_time_use_full <= plot_range.end)
    x_time_use = x_time_use_full[mask]
    cp_vals_use = cp_vals_use_full[mask]
    
    # Determine gains reference point
    if gains_ref_mode == 'plot_range':
        # Use first point in visible range as t0
        t0_idx = 0
    else:  # 'alert_driven'
        # Use last withdrawal transaction with keyword (existing logic)
        t0_idx = ...  # from reference_state logic
    
    # Calculate gains from t0_idx
    gains = cp_vals_use - cp_vals_use[t0_idx]
    
    # ... rest of plot generation ...
```

---

## Part 2: Data Aggregation for Performance

### 2.1 Problem Statement

**Current Performance Issue**:
- Backend updates at ~seconds to minutes cadence
- For 7+ days of data at high frequency → 10,000+ datapoints
- No aggregation before plotting → slow dashboard refresh
- Only affects plots, NOT decision logic

**Goal**: Aggregate data on a time grid with statistical summaries (percentiles, median) for fast rendering.

---

### 2.2 Required Changes

#### 2.2.1 Configuration Schema

**File**: `orchestrator_config.yaml`

**Updated Section**:
```yaml
visualization:
  diagnostics:
    # ... existing fields ...
    aggregation:
      enabled: false           # Toggle aggregation on/off
      method: "whiskers"       # "whiskers" | "ohlc" | "none"
      time_unit: "5min"        # "1min" | "5min" | "15min" | "1h" | "6h" | "1d"
      percentiles: [10, 25, 50, 75, 90]  # For whisker plots
      # Whiskers show: [p10-p25 box, p25-p50 box, median line, p50-p75 box, p75-p90 box]
```

#### 2.2.2 Aggregation Engine

**New File**: `src/core/aggregation.py`

```python
#!/usr/bin/env python3
"""
Time-based aggregation for dashboard plots (performance optimization).
Does NOT affect decision logic, only visualization.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Tuple, List, Optional

def aggregate_timeseries(
    timestamps: np.ndarray,
    values: np.ndarray,
    time_unit: str = "5min",
    percentiles: List[float] = [10, 25, 50, 75, 90]
) -> Tuple[np.ndarray, dict]:
    """
    Aggregate time series data into fixed time bins with statistical summaries.
    
    Args:
        timestamps: Array of datetime objects
        values: Array of numeric values
        time_unit: Aggregation interval ("1min", "5min", "15min", "1h", "6h", "1d")
        percentiles: List of percentile values to compute (e.g., [10, 25, 50, 75, 90])
    
    Returns:
        Tuple of (bin_centers, stats_dict)
        - bin_centers: Array of datetime objects (center of each bin)
        - stats_dict: {
            'p10': array,  # 10th percentile per bin
            'p25': array,  # 25th percentile
            'median': array,  # 50th percentile
            'p75': array,
            'p90': array,
            'count': array,  # Number of samples per bin
          }
    """
    # Convert to pandas for efficient groupby
    df = pd.DataFrame({
        'timestamp': pd.to_datetime(timestamps),
        'value': values
    })
    
    # Set timestamp as index
    df.set_index('timestamp', inplace=True)
    
    # Resample with specified frequency
    freq_map = {
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "1h": "1H",
        "6h": "6H",
        "1d": "1D"
    }
    freq = freq_map.get(time_unit, "5min")
    
    # Group and compute percentiles
    grouped = df.resample(freq)
    
    stats = {}
    for p in percentiles:
        stats[f'p{int(p)}'] = grouped['value'].quantile(p/100.0).values
    
    stats['count'] = grouped['value'].count().values
    
    # Bin centers (use label='left' to get bin start, then add half period)
    bin_centers = grouped['value'].median().index.to_pydatetime()
    
    return bin_centers, stats


def should_aggregate(
    n_points: int,
    time_span_days: float,
    threshold_points: int = 1000
) -> bool:
    """
    Determine if aggregation should be applied based on data density.
    
    Args:
        n_points: Number of data points
        time_span_days: Time span in days
        threshold_points: Threshold above which aggregation is recommended
    
    Returns:
        True if aggregation should be applied
    """
    return n_points > threshold_points
```

#### 2.2.3 Plot Updates for Whiskers

**File**: `src/core/diagnostics.py` (`plot_residual_composite`, lines 45-439)

**New Parameters**:
```python
def plot_residual_composite(
    *,
    # ... existing params ...
    # NEW: Aggregation support
    aggregated_data: Optional[dict] = None,  # If provided, use whisker plot
    aggregation_time_unit: Optional[str] = None,
) -> str:
```

**Modified Plotting Logic**:
```python
# In main panel (corrected positions plot)
if aggregated_data is not None:
    # Plot whisker boxes instead of line
    bin_centers = aggregated_data['bin_centers']
    
    # Draw percentile boxes
    ax_main.fill_between(
        bin_centers,
        aggregated_data['p10'],
        aggregated_data['p25'],
        color='lightblue', alpha=0.3, label='p10-p25'
    )
    ax_main.fill_between(
        bin_centers,
        aggregated_data['p25'],
        aggregated_data['p50'],
        color='blue', alpha=0.4, label='p25-p50'
    )
    ax_main.plot(bin_centers, aggregated_data['p50'], 'b-', linewidth=2, label='Median')
    ax_main.fill_between(
        bin_centers,
        aggregated_data['p50'],
        aggregated_data['p75'],
        color='blue', alpha=0.4, label='p50-p75'
    )
    ax_main.fill_between(
        bin_centers,
        aggregated_data['p75'],
        aggregated_data['p90'],
        color='lightblue', alpha=0.3, label='p75-p90'
    )
else:
    # Original line plot
    ax_main.plot(ts, y_plot, label="Corrected Position", color="#2E86AB", marker="s", markersize=markersize, linewidth=1.5)
```

#### 2.2.4 Dashboard Integration

**File**: `src/core/exporter.py` (`_build_chart_b64`)

**Modified Logic**:
```python
def _build_chart_b64(self, asset: str, view: str, source: str) -> tuple[...]:
    # ... data fetching ...
    
    # Check if aggregation should be applied
    agg_cfg = getattr(self.settings.orchestrator.diagnostics, 'aggregation', None)
    should_agg = (
        agg_cfg and 
        getattr(agg_cfg, 'enabled', False) and
        len(x_time_use) > 1000  # Threshold
    )
    
    aggregated_data = None
    if should_agg:
        from .aggregation import aggregate_timeseries
        time_unit = getattr(agg_cfg, 'time_unit', '5min')
        percentiles = getattr(agg_cfg, 'percentiles', [10, 25, 50, 75, 90])
        
        bin_centers, stats = aggregate_timeseries(
            timestamps=x_time_use,
            values=cp_vals_use,
            time_unit=time_unit,
            percentiles=percentiles
        )
        
        aggregated_data = {
            'bin_centers': bin_centers,
            **stats
        }
        
        log.info(f"Aggregated {len(x_time_use)} points to {len(bin_centers)} bins ({time_unit})")
    
    # Pass to plot function
    out_path = plot_residual_composite(
        # ... existing params ...
        aggregated_data=aggregated_data,
        aggregation_time_unit=time_unit if should_agg else None,
    )
```

**New HTML Controls**:
```html
<div class="aggregation-controls" style="margin-top: 1rem; padding: 10px; background: #f8f9fa; border: 1px solid #ddd;">
  <strong>Data Aggregation (for large datasets):</strong><br/>
  <input type="checkbox" id="aggEnabled" onchange="updateAggregation()" />
  <label for="aggEnabled">Enable Aggregation</label>
  <label>Time Unit:
    <select id="aggTimeUnit" onchange="updateAggregation()">
      <option value="1min">1 minute</option>
      <option value="5min" selected>5 minutes</option>
      <option value="15min">15 minutes</option>
      <option value="1h">1 hour</option>
      <option value="6h">6 hours</option>
      <option value="1d">1 day</option>
    </select>
  </label>
  <span style="font-size: 0.85em; color: #666; margin-left: 1rem;">
    (Aggregation reduces plot refresh time for large datasets)
  </span>
</div>

<script>
async function updateAggregation() {
  const enabled = document.getElementById('aggEnabled').checked;
  const timeUnit = document.getElementById('aggTimeUnit').value;
  
  const res = await fetch('/api/update-aggregation', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      asset: getCurrentAsset(),
      enabled: enabled,
      time_unit: timeUnit
    })
  });
  
  if (res.ok) {
    window.location.reload();
  }
}
</script>
```

---

## Part 3: Impact Analysis & Safety

### 3.1 Scope Isolation

**What IS Affected**:
- ✅ Dashboard plots (`diagnostics.py`, `exporter.py`)
- ✅ User-facing visualization controls (HTML/JS)
- ✅ Configuration schema (`orchestrator_config.yaml`)
- ✅ Settings dataclasses (`settings.py`)

**What is NOT Affected** (Safety Guarantees):
- ❌ Decision logic (`alert_logic.py`) - Uses original data range
- ❌ Residual calculations - No aggregation applied
- ❌ Polynomial fits - Computed on full data
- ❌ Sigma calculations - Uses all residuals
- ❌ Withdrawal limits (Wmax) - Based on precise gains
- ❌ Prometheus metrics - Real-time, not aggregated

### 3.2 Backward Compatibility

**Configuration**:
- All new fields are OPTIONAL with sensible defaults
- `mode: "inherit"` → Existing behavior preserved
- `aggregation.enabled: false` → No performance change

**API**:
- New endpoints (`/api/update-plot-range`, `/api/update-aggregation`) are additive
- Existing endpoints unchanged

### 3.3 Testing Strategy

**Unit Tests** (new file: `tests/test_plot_range_control.py`):
```python
def test_plot_range_resolution_inherit():
    """Test inherit mode preserves data range"""
    ...

def test_plot_range_resolution_custom():
    """Test custom range overrides"""
    ...

def test_plot_range_resolution_relative():
    """Test relative duration calculation"""
    ...

def test_data_refresh_trigger():
    """Test automatic data expansion when plot range exceeds sync range"""
    ...
```

**Unit Tests** (new file: `tests/test_aggregation.py`):
```python
def test_aggregate_timeseries_5min():
    """Test 5-minute aggregation with percentiles"""
    ...

def test_aggregate_timeseries_empty():
    """Test handling of empty data"""
    ...

def test_should_aggregate_threshold():
    """Test aggregation trigger logic"""
    ...
```

**Integration Tests** (new file: `tests/test_dashboard_plot_features.py`):
```python
def test_plot_range_update_api():
    """Test POST /api/update-plot-range"""
    ...

def test_gains_reference_plot_mode():
    """Test gains calculation with plot_range reference"""
    ...

def test_aggregation_preserves_decision_logic():
    """Verify decision logic uses non-aggregated data"""
    ...
```

---

## Part 4: Implementation Sequence

### Phase A: Configuration & Data Model (2-3 days)

1. ✅ Update `orchestrator_config.yaml` schema
2. ✅ Add `PlotRangeConfig` and aggregation dataclasses to `settings.py`
3. ✅ Update config loader in `_load_v2_schema()`
4. ✅ Add validation logic
5. ✅ Write unit tests for config parsing

**Deliverable**: Config changes load successfully, tests pass

### Phase B: Plot Range Control - Backend (3-4 days)

1. ✅ Add `_plot_range_overrides` dict to `MetricsExporter.__init__()`
2. ✅ Implement `PlotRangeConfig.resolve()` method
3. ✅ Update `_build_chart_b64()` to:
   - Read plot range override
   - Filter data to plot range
   - Handle gains reference mode
4. ✅ Add `_schedule_data_refresh()` method
5. ✅ Add `POST /api/update-plot-range` endpoint
6. ✅ Write unit tests for range logic

**Deliverable**: Backend can handle plot range updates programmatically

### Phase C: Plot Range Control - Frontend (2 days)

1. ✅ Add HTML controls for plot range selection
2. ✅ Add JavaScript for API communication
3. ✅ Add gains reference radio buttons
4. ✅ Update HTML to show current plot range
5. ✅ Test in browser

**Deliverable**: Users can adjust plot range via dashboard UI

### Phase D: Aggregation Engine (2-3 days)

1. ✅ Create `src/core/aggregation.py`
2. ✅ Implement `aggregate_timeseries()` function
3. ✅ Implement `should_aggregate()` logic
4. ✅ Write unit tests for aggregation
5. ✅ Test with synthetic data (10k+ points)

**Deliverable**: Aggregation module works standalone

### Phase E: Aggregation Integration (2-3 days)

1. ✅ Update `plot_residual_composite()` to accept aggregated data
2. ✅ Add whisker plot rendering logic
3. ✅ Update `_build_chart_b64()` to call aggregation
4. ✅ Add `POST /api/update-aggregation` endpoint
5. ✅ Add HTML controls for aggregation settings
6. ✅ Test with real database queries

**Deliverable**: Dashboard renders whisker plots for large datasets

### Phase F: Integration Testing & Documentation (2 days)

1. ✅ Run full integration test suite
2. ✅ Performance benchmarks (measure speedup)
3. ✅ Update `ARCHITECTURE.md`
4. ✅ Update `API.md` with new endpoints
5. ✅ User guide for new features

**Deliverable**: Feature complete, documented, and tested

### Phase G: Review & Merge (1 day)

1. ✅ Code review
2. ✅ Address feedback
3. ✅ Merge to main

**Total Estimated Time**: 14-18 days

---

## Part 5: Open Questions & Clarifications Needed

### Question 1: Plot Range Persistence
**Question**: Should plot range selections persist across browser sessions?

**Options**:
- A) Session-only (in-memory, lost on refresh) - SIMPLER
- B) Per-user config file (requires user auth system)
- C) Browser localStorage (per-browser persistence)

**Recommendation**: Start with A (session-only), add C (localStorage) in future.
>> I agree:  session based for the moment.
---

### Question 2: Gains Reference Fallback Chain
**Current**: `keyword → fallback (data_range)`  
**Proposed**: `keyword → plot_fallback (plot_range) → fallback (data_range)`

**Clarification Needed**:
- If user selects "plot_range" gains mode but plot_range < data_range, should we:
  - A) Use plot_range.start even if it's recent (might show negative gains)
  - B) Fall back to keyword mode automatically
  - C) Show warning and let user decide

**Recommendation**: A (use plot_range as specified), with UI warning if gains are negative.
>> The proposed fallback chain is what I expect. I would just add a very general remark here with respect to the web-UI: If a configuration is modified by user web-UI but is impossible to do, then you come back to what is in the configuration file. The fallback is the default and the default is the configuration file. No hardcoded buried fallback (everything explicit). I think you know that, but just in case, I repeated it here.

---

### Question 3: Aggregation Interaction with Overlays
**Issue**: Trend/decision overlays are computed on full data. If main series is aggregated, overlays might not align.

**Options**:
- A) Also aggregate overlays (consistent but loses precision)
- B) Keep overlays full-resolution (visually accurate but mixed granularity)
- C) Disable overlays when aggregation is on

**Recommendation**: B (mixed granularity), clearly document in tooltip.
>>  Obviously this should not be aggregated. Only the raw datapoints are concerned by the change. A ticking box should be added, If the user need to see the raw datapoint instead of the whisker plots (with a warning the coming back to the raw repesentation can be slow)
---

### Question 4: Auto-Aggregation Trigger
**Current Plan**: Auto-enable aggregation when `n_points > 1000`

**Clarification Needed**:
- Should threshold be configurable per-asset?
- Should user see notification when auto-aggregation kicks in?

**Recommendation**: 
- Global threshold (1000 points)
- Show notification: "Large dataset detected (N points). Aggregation enabled automatically. [Disable]"
>> There is no need of auto-aggregation based on trigger. The n_points is irrelevant here. If the there is only 1 single data point in the window, the whisker box should just fall to that unique value ( It will look like an horizontal line "--" instead of a box). If there is 0 datapoint in the whisker box, then this specific whisker box will not be shown. It is up to the user to change the aggregation range (1m, 5m, 10m, etc...) to fix visualisation issues.
---

### Question 5: Data Sync Expansion Strategy
**Scenario**: User requests plot range [Jan 1 - Jan 31], but data range is [Jan 15 - Jan 30].

**Options**:
- A) Expand data range to [Jan 1 - Jan 31] and sync all assets
- B) Expand only for requested asset
- C) Warn user and clip to available range

**Recommendation**: B (per-asset expansion), show spinner during sync.
>> For the requested asset, you still show the [Jan 1 - Jan 31] range requested by the user. The user will see that there is a huge zone without data and can adjust accordingly. So If I understood correctly your choice, this is choice B.
---

## Part 6: Risk Assessment

### High Risk ⚠️
1. **Data Range Confusion**: Users might confuse plot range with decision range
   - **Mitigation**: Clear UI labels, tooltips, documentation

2. **Performance Regression**: Background data sync might slow down orchestrator
   - **Mitigation**: Use daemon threads, rate limiting, async design

3. **Aggregation Bugs**: Incorrect percentile calculations could mislead users
   - **Mitigation**: Extensive unit tests, validate against pandas

### Medium Risk ⚡
1. **Config Complexity**: More knobs → more confusion
   - **Mitigation**: Sensible defaults, progressive disclosure in UI

2. **Browser Compatibility**: Date-time inputs vary across browsers
   - **Mitigation**: Test on Chrome, Firefox, Safari; fallback to text input

### Low Risk ✓
1. **Backward Compatibility**: New fields are optional
2. **Decision Logic Isolation**: Changes don't touch core logic

---

## Part 7: Success Metrics

1. **Performance**: Dashboard refresh time reduced by >50% for 7-day windows
2. **Usability**: Users can zoom into specific time periods without config edits
3. **Flexibility**: Gains can be calculated from either alert-driven or plot-driven reference
4. **Stability**: No regressions in existing decision logic or metrics

---

## Appendix A: File Modification Checklist

### Configuration
- [ ] `config/orchestrator_config.yaml` - Add plot_range and aggregation sections

### Core Logic
- [ ] `src/core/settings.py` - Add PlotRangeConfig, update DiagnosticsConfig
- [ ] `src/core/aggregation.py` - NEW FILE - Aggregation engine
- [ ] `src/core/exporter.py` - Plot range logic, new API endpoints, HTML controls
- [ ] `src/core/diagnostics.py` - Whisker plot rendering
- [ ] `src/core/reference_state.py` - Plot range fallback logic (minor)

### Tests
- [ ] `tests/test_plot_range_control.py` - NEW FILE
- [ ] `tests/test_aggregation.py` - NEW FILE
- [ ] `tests/test_dashboard_plot_features.py` - NEW FILE

### Documentation
- [ ] `docs/ARCHITECTURE.md` - Update data flow diagrams
- [ ] `docs/API.md` - Document new endpoints
- [ ] `docs/USER_GUIDE_PLOT_RANGE.md` - NEW FILE - User guide

---

## Appendix B: API Specification

### POST /api/update-plot-range

**Request**:
```json
{
  "asset": "usdc",
  "start": "2025-10-01T00:00:00Z",
  "end": "2025-11-01T00:00:00Z",
  "gains_reference": "plot_range"
}
```

**Response (Success)**:
```json
{
  "success": true,
  "data_refresh_triggered": false,
  "effective_range": {
    "start": "2025-10-01T00:00:00Z",
    "end": "2025-11-01T00:00:00Z"
  }
}
```

**Response (Data Refresh Needed)**:
```json
{
  "success": true,
  "data_refresh_triggered": true,
  "message": "Requested range exceeds synced data. Background refresh started.",
  "eta_seconds": 30
}
```

### POST /api/update-aggregation

**Request**:
```json
{
  "asset": "usdc",
  "enabled": true,
  "time_unit": "5min"
}
```

**Response**:
```json
{
  "success": true,
  "aggregated_points": 288,
  "original_points": 8640
}
```

---

## Appendix C: Architecture Diagrams

### Current Data Flow
```
orchestrator_config.yaml
  └─ data.date_range (2025-10-01 to 2025-11-22)
      ├─ Greptime Query ───┐
      └─ Plot Generation ──┤ SAME RANGE
                           └─> Dashboard
```

### Proposed Data Flow
```
orchestrator_config.yaml
  ├─ data.date_range (2025-10-01 to 2025-11-22) ─┐
  │                                               │
  └─ visualization.diagnostics.plot_range         │
       (2025-11-01 to 2025-11-22)                 │
                                                  │
                    ┌─────────────────────────────┘
                    │
                    ├─> Greptime Query (FULL RANGE)
                    │     └─> Data Cache [10k points]
                    │
                    └─> Plot Filter (PLOT RANGE)
                          ├─> Aggregation? [Yes: 288 bins]
                          └─> Dashboard
```

---

**End of Implementation Plan**
