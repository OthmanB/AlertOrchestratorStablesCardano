# Alert Orchestrator Troubleshooting Guide

This guide covers common issues, their causes, and solutions when running the Alert Orchestrator.

## Table of Contents

1. [Connection Issues](#connection-issues)
2. [Configuration Problems](#configuration-problems)
3. [Data Issues](#data-issues)
4. [Decision Logic Issues](#decision-logic-issues)
5. [Dashboard & Metrics Issues](#dashboard--metrics-issues)
6. [Performance Problems](#performance-problems)
7. [Diagnostic Tools](#diagnostic-tools)
8. [Common Error Messages](#common-error-messages)

---

## Connection Issues

### Issue: "Failed to connect to GreptimeDB"

**Symptoms**:
```
ERROR - Failed to connect to GreptimeDB: Connection refused
```

**Causes**:
1. GreptimeDB not running
2. Incorrect host/port configuration
3. Firewall blocking connection
4. Network connectivity issues

**Solutions**:

1. **Check GreptimeDB is running**:
   ```bash
   # Check if GreptimeDB is listening
   lsof -i :4000  # Default GreptimeDB HTTP port
   
   # Or using netstat
   netstat -an | grep 4000
   ```

2. **Verify configuration**:
   ```yaml
   # config/orchestrator_config.yaml
   client:
     greptime:
       host: "http://localhost"   # Should include protocol
       port: 4000
       database: "liqwid"
       timeout: 10
   ```

3. **Test connection manually**:
   ```bash
   curl -X POST http://localhost:4000/v1/sql \
     -d "sql=SELECT 1" \
     -d "db=liqwid"
   ```

4. **Check logs for details**:
   ```bash
   python -m src.main --log-level DEBUG
   ```

### Issue: "Liqwid API timeout"

**Symptoms**:
```
WARNING - Liqwid API request timed out after 10 seconds
```

**Causes**:
1. API endpoint unreachable
2. Network latency
3. API rate limiting
4. Timeout too short

**Solutions**:

1. **Test API endpoint**:
   ```bash
   curl -X POST https://api.liqwid.finance/graphql \
     -H "Content-Type: application/json" \
     -d '{"query": "{ __typename }"}'
   ```

2. **Increase timeout**:
   ```yaml
   orchestrator:
     transaction_sync:
       timeout_seconds: 30   # Default: 10
   ```

3. **Check network connectivity**:
   ```bash
   ping api.liqwid.finance
   traceroute api.liqwid.finance
   ```

---

## Configuration Problems

### Issue: "Assets list is empty"

**Symptoms**:
```
ERROR - assets list is empty
```
Dashboard shows: `_all_: decision=-1 (ERROR)`

**Cause**: `client.assets` not configured or empty list

**Solution**:

```yaml
# config/orchestrator_config.yaml
client:
  assets: ["djed", "usdm", "wanusdc", "wanusdt"]  # Must be non-empty
```

### Issue: "Asset resolution failed"

**Symptoms**:
```
ERROR - asset resolution failed for USDT
```

**Causes**:
1. Asset not in GreptimeDB (no table `liqwid_supply_positions_<asset>`)
2. Liqwid API can't resolve asset policy ID
3. Asset name misspelled

**Solutions**:

1. **Check asset exists in database**:
   ```sql
   -- Connect to GreptimeDB
   SHOW TABLES LIKE 'liqwid_supply_positions_%';
   ```

2. **Verify asset name**:
   - Correct: `"wanusdt"` (lowercase, with prefix)
   - Incorrect: `"USDT"`, `"usdt"` (may work if resolver handles it)

3. **Check available assets**:
   ```bash
   python -m src.main --once --log-level DEBUG
   # Look for "Resolved asset: USDT → wanusdt" messages
   ```

### Issue: "Token registry resolution failed"

**Symptoms**:
```
ERROR - Token registry resolution failed for assets: djed, usdm
Please update config/token_registry.csv with rows: asset,policy_id,token_name_hex
```

**Cause**: Token registry missing or incomplete (required for Minswap price sources)

**Solution**:

1. **Create token registry**:
   ```csv
   # config/token_registry.csv
   asset,policy_id,token_name_hex
   djed,8db269c3ec630e06ae29f74bc39edd1f87c819f1056206e879a1cd61,446a656432
   usdm,c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad,0014df105553444d
   ```

2. **Find policy ID and token name**:
   - Go to Minswap token page for your asset
   - Look for "Policy ID" and "Token Name (Hex)"
   - Or query Liqwid API for asset metadata

3. **Disable Minswap price source** (if not needed):
   ```yaml
   orchestrator:
     apis:
       liqwid_graphql: "https://api.liqwid.finance/graphql"
       # minswap_aggregator: null  # Comment out or remove
   ```

### Issue: "Invalid configuration"

**Symptoms**:
```
ERROR - SettingsError: Invalid value for safety_factor.c: must be in (0, 1]
```

**Causes**: Configuration validation failed

**Solutions**:

1. **Use config doctor**:
   ```bash
   python -m src.main --print-config-normalized
   ```
   This shows the normalized configuration with all defaults applied and highlights errors.

2. **Common validation errors**:
   - `safety_factor.c`: Must be > 0 and ≤ 1 (e.g., 0.5)
   - `interval_minutes`: Must be positive integer
   - `k_sigma`: Must be positive (typical: 2.0 to 3.0)
   - `polynomial_order`: Must be non-negative integer (typical: 2 to 4)

3. **Check YAML syntax**:
   ```bash
   # Use a YAML validator
   python -c "import yaml; yaml.safe_load(open('config/orchestrator_config.yaml'))"
   ```

---

## Data Issues

### Issue: "No tagged withdrawal found"

**Symptoms**:
```
WARNING - No tagged withdrawal found for asset=djed (resolved=djed); fallback=null
```
Decision: HOLD, W_max = 0

**Causes**:
1. No withdrawals tagged with `reference_keyword` in database
2. Reference keyword misspelled
3. Withdrawals exist but outside date range

**Solutions**:

1. **Check for tagged withdrawals**:
   ```sql
   SELECT ts, wallet_address, amount, memo
   FROM liqwid_withdrawals_djed
   WHERE memo = 'alert_driven_withdrawal'
   ORDER BY ts DESC
   LIMIT 1;
   ```

2. **Use fallback mode**:
   ```yaml
   orchestrator:
     reference_keyword: "alert_driven_withdrawal"
     reference_keyword_fallback: "data_range"  # Use date_range.start as t0
   
   client:
     date_range:
       start: "2025-01-01T00:00:00Z"  # Set explicit start date
   ```

3. **Tag a withdrawal** (manual):
   ```sql
   -- Update a withdrawal to tag it
   INSERT INTO liqwid_withdrawals_djed (ts, wallet_address, amount, tx_hash, memo)
   VALUES ('2025-01-01T00:00:00Z', 'addr1...', 1000.0, 'abc123...', 'alert_driven_withdrawal');
   ```

### Issue: "No position data available"

**Symptoms**:
```
WARNING - No position data for asset=djed in date range
```
Decision: HOLD, W_max = 0

**Causes**:
1. No data in `liqwid_supply_positions_<asset>` table
2. Date range too narrow or outside available data
3. Table name mismatch

**Solutions**:

1. **Check position data exists**:
   ```sql
   SELECT COUNT(*), MIN(ts), MAX(ts)
   FROM liqwid_supply_positions_djed;
   ```

2. **Adjust date range**:
   ```yaml
   client:
     date_range:
       start: null  # Use all available data
       end: null
   ```

3. **Check table prefix**:
   ```yaml
   client:
     table_asset_prefix: "liqwid_supply_positions_"  # Must match actual table names
   ```

4. **Sync position data** (if using custom ingestion):
   ```bash
   # Run your data ingestion script
   python scripts/sync_liqwid_positions.py
   ```

### Issue: "Insufficient data points for decision gate"

**Symptoms**:
```
WARNING - Insufficient data points (5) for decision gate (min: 10)
```
Decision gate skipped

**Causes**:
1. Not enough historical data
2. `min_points` threshold too high
3. `lookback_hours` too narrow

**Solutions**:

1. **Lower min_points threshold**:
   ```yaml
   orchestrator:
     decision_gate:
       min_points: 5  # Default: 10
   ```

2. **Increase lookback window**:
   ```yaml
   orchestrator:
     decision_gate:
       lookback_hours: 72.0  # Default: 48.0 (3 days)
   ```

3. **Wait for more data to accumulate**:
   - Position data needs time to build up
   - Check back after 24-48 hours

---

## Decision Logic Issues

### Issue: "W_max always 0"

**Symptoms**: Dashboard shows `decision=0` (HOLD) or `wmax_usd=0.00` for all assets

**Causes**:

1. **Non-positive gains**:
   - Corrected gain $G(t_1) \leq 0$ (losses or no change since reference point)
   - Check `g_usd` metric or dashboard

2. **Residual gate triggered**:
   - $|r(t_1)| > k \cdot \sigma$ (anomaly detected)
   - Check `residual_trigger` metric (1 = triggered)

3. **Safety factor too high**:
   - If `c` is very small (e.g., 0.01), W_max will be tiny
   - Check configuration

4. **No reference point**:
   - Fallback mode = "null" and no tagged withdrawal found
   - See "No tagged withdrawal found" section

**Diagnosis Steps**:

1. **Check gains**:
   ```bash
   # Run once and look for g_usd values
   python -m src.main --once
   ```
   Output: `djed: decision=0, wmax_usd=0.00 [reason: non-positive gains since t0 (clamped)]`

2. **Check residual gating**:
   - Open dashboard: `http://localhost:9808/dashboard?asset=djed`
   - Look for "Residual Trigger: ⚠️ TRIGGERED" badge
   - Check diagnostic chart: Is latest residual (red dot) outside red dashed lines?

3. **Check Prometheus metrics**:
   ```bash
   curl -s http://localhost:9808/metrics | grep "wo_g_usd"
   curl -s http://localhost:9808/metrics | grep "wo_residual_trigger"
   ```

**Solutions**:

1. **Wait for positive gains**:
   - If market is down, gains may be negative
   - W_max = 0 is correct behavior (prevents withdrawing losses)

2. **Adjust residual gate threshold**:
   ```yaml
   orchestrator:
     decision_gate:
       k_sigma: 3.0  # Increase threshold (default: 2.0)
       # Or disable temporarily
       enabled: false
   ```

3. **Increase safety factor**:
   ```yaml
   orchestrator:
     safety_factor:
       c: 0.8  # More aggressive (default: 0.5)
   ```

4. **Set fallback reference mode**:
   ```yaml
   orchestrator:
     reference_keyword_fallback: "data_range"
   
   client:
     date_range:
       start: "2025-01-01T00:00:00Z"
   ```

### Issue: "Residual gate always triggered"

**Symptoms**: `residual_trigger=1` for all assets, always HOLD

**Causes**:
1. Threshold (`k_sigma`) too low
2. High volatility in corrected position series
3. Poor baseline fit (wrong polynomial order or method)
4. Not enough data for stable σ calculation

**Solutions**:

1. **Increase k_sigma**:
   ```yaml
   orchestrator:
     decision_gate:
       k_sigma: 3.0  # Or even 4.0 for very volatile assets
   ```

2. **Adjust polynomial order**:
   ```yaml
   orchestrator:
     decision_gate:
       polynomial_order: 3  # Try higher order (default: 2)
       # Or switch to median baseline
       method: "median"
   ```

3. **Exclude last point from σ calculation**:
   ```yaml
   orchestrator:
     decision_gate:
       exclude_last_for_sigma: true  # Don't include current point in σ
   ```

4. **Increase lookback window**:
   ```yaml
   orchestrator:
     decision_gate:
       lookback_hours: 72.0  # More data for stable σ
   ```

5. **Check diagnostic chart**:
   - Open `output/<asset>_<timestamp>_residual_composite.png`
   - Look at residual plot panel
   - Is the baseline (orange dashed) fitting well?
   - Are residuals evenly distributed or clustered?

### Issue: "Price mismatch always flagged"

**Symptoms**: `price_mismatch=1` for all assets

**Causes**:
1. Price sources returning different prices (expected for different exchanges)
2. Threshold too tight
3. One price source stale/incorrect

**Solutions**:

1. **Check price deltas**:
   ```bash
   curl -s http://localhost:9808/metrics | grep "wo_price_delta"
   ```
   Example output: `wo_price_delta_rel{asset="djed"} 0.02` (2% difference)

2. **Adjust threshold**:
   ```yaml
   orchestrator:
     analysis_v2:
       price_compare:
         relative_threshold: 0.05  # 5% difference allowed (default: 0.02)
   ```

3. **Disable price comparison** (if not needed):
   ```yaml
   orchestrator:
     telemetry:
       expose:
         price_mismatch: false
   ```

---

## Dashboard & Metrics Issues

### Issue: "Dashboard not loading"

**Symptoms**: Browser shows "Connection refused" or "This site can't be reached"

**Causes**:
1. HTTP server not started
2. Wrong port or address
3. Firewall blocking port
4. Telemetry disabled

**Solutions**:

1. **Check server is running**:
   ```bash
   lsof -i :9808  # Check if port 9808 is listening
   ```

2. **Enable telemetry**:
   ```yaml
   orchestrator:
     telemetry:
       enabled: true  # Must be true
       listen_address: "0.0.0.0"  # Or "127.0.0.1" for localhost only
       listen_port: 9808
   ```

3. **Check logs for startup message**:
   ```
   INFO - Starting HTTP server on 0.0.0.0:9808
   ```

4. **Test with curl**:
   ```bash
   curl http://localhost:9808/dashboard
   ```

5. **Check firewall**:
   ```bash
   # macOS
   sudo /usr/libexec/ApplicationFirewall/socketfilterfw --listapps
   
   # Linux (iptables)
   sudo iptables -L -n | grep 9808
   ```

### Issue: "Metrics endpoint returns 404"

**Symptoms**: `http://localhost:9808/metrics` returns "404 Not Found"

**Causes**:
1. Wrong path configuration
2. Telemetry disabled

**Solutions**:

1. **Check configured path**:
   ```yaml
   orchestrator:
     telemetry:
       path: "/metrics"  # Default, should match
   ```

2. **Test endpoint**:
   ```bash
   curl http://localhost:9808/metrics
   ```

3. **Check logs**:
   ```bash
   python -m src.main --log-level DEBUG
   # Look for "Serving /metrics endpoint" message
   ```

### Issue: "Dashboard shows no data"

**Symptoms**: Dashboard loads but shows empty tables, no charts

**Causes**:
1. No evaluations run yet (first iteration pending)
2. All assets have errors
3. JavaScript error in browser

**Solutions**:

1. **Wait for first evaluation**:
   - Check interval: `schedule.interval_minutes` (default: 60)
   - Or trigger manually: restart with `--once` flag to populate data immediately

2. **Check API endpoint**:
   ```bash
   curl http://localhost:9808/api/decisions
   ```
   Should return JSON with asset decisions.

3. **Check browser console**:
   - Open browser DevTools (F12)
   - Look for JavaScript errors in Console tab
   - Check Network tab for failed API requests

4. **Force refresh**:
   - Hard refresh: Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (macOS)

### Issue: "Charts not displaying"

**Symptoms**: Dashboard shows broken image icons or missing charts

**Causes**:
1. Diagnostic charts not generated
2. File path mismatch
3. Chart generation error

**Solutions**:

1. **Enable diagnostics**:
   ```yaml
   orchestrator:
     diagnostics:
       enabled: true
       dir: "output"
   ```

2. **Check output directory**:
   ```bash
   ls -lh output/
   # Should show .png files like: djed_20250121_123456_residual_composite.png
   ```

3. **Check logs for chart generation errors**:
   ```bash
   python -m src.main --log-level DEBUG
   # Look for "Generating diagnostic chart" messages
   ```

4. **Test chart generation manually**:
   ```bash
   python -c "import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.savefig('test.png')"
   # If this fails, matplotlib installation issue
   ```

---

## Performance Problems

### Issue: "Evaluations taking too long"

**Symptoms**: Evaluation loop takes > 5 minutes, delays between iterations

**Causes**:
1. Too many assets configured
2. Large date ranges
3. Slow GreptimeDB queries
4. Chart generation overhead

**Solutions**:

1. **Profile evaluation time**:
   ```bash
   python -m src.main --once --log-level DEBUG
   # Check timestamps in logs to identify bottlenecks
   ```

2. **Reduce asset list** (if possible):
   ```yaml
   client:
     assets: ["djed", "usdm"]  # Monitor fewer assets
   ```

3. **Limit lookback period**:
   ```yaml
   client:
     date_range:
       start: "2025-01-15T00:00:00Z"  # Only last week
   
   orchestrator:
     decision_gate:
       lookback_hours: 24.0  # Only last 24 hours
   ```

4. **Disable expensive features**:
   ```yaml
   orchestrator:
     diagnostics:
       enabled: false  # Skip chart generation
     
     decision_gate:
       enabled: false  # Skip residual analysis
   ```

5. **Optimize GreptimeDB queries**:
   - Add indexes on `ts` column (usually automatic)
   - Check GreptimeDB performance metrics
   - Consider increasing GreptimeDB resources

### Issue: "High memory usage"

**Symptoms**: Python process using > 500 MB RAM

**Causes**:
1. Large time series data in memory
2. Too many assets
3. Memory leak (rare)

**Solutions**:

1. **Monitor memory**:
   ```bash
   ps aux | grep python
   # Or use htop/top
   ```

2. **Reduce data volume** (see "Evaluations taking too long")

3. **Restart periodically**:
   ```bash
   # Use a process manager (systemd, supervisor) to restart daily
   # Example systemd timer: restart every 24 hours
   ```

---

## Diagnostic Tools

### 1. Config Doctor

**Purpose**: Validate and inspect normalized configuration

**Usage**:
```bash
python -m src.main --print-config-normalized | jq
```

**Output**: JSON with all configuration including defaults and per-asset overrides

**Use Cases**:
- Verify configuration loaded correctly
- Check default values applied
- Debug per-asset override logic

### 2. One-Shot Mode

**Purpose**: Run single evaluation and exit (no metrics server)

**Usage**:
```bash
python -m src.main --once
```

**Output**: Console summary of decisions per asset

**Use Cases**:
- Quick testing after config changes
- Debugging without starting server
- Scripting/automation

### 3. Debug Logging

**Purpose**: Detailed execution trace

**Usage**:
```bash
python -m src.main --log-level DEBUG
```

**Output**: Verbose logs including:
- SQL queries
- API requests/responses
- Calculation steps
- Timing information

**Use Cases**:
- Troubleshoot connection issues
- Inspect data fetching
- Profile performance

### 4. Test Mode

**Purpose**: Safe testing with separate tables

**Usage**:
```bash
python -m src.main --test-prefix
```

**Behavior**:
- Reads from normal tables (`liqwid_supply_positions_*`)
- Writes to test tables (`test_liqwid_deposits_*`, etc.)
- Dashboard/metrics work normally

**Use Cases**:
- Test transaction sync without affecting prod data
- Validate configuration changes safely

### 5. Diagnostic Charts

**Purpose**: Visual analysis of position data and residuals

**Location**: `output/<asset>_<timestamp>_residual_composite.png`

**Panels**:
1. Position view: Raw position + smoothed baseline + transactions
2. Residual plot: Deviations from baseline with threshold bands
3. KDE distribution: Probability density of residuals

**Use Cases**:
- Debug residual gating behavior
- Inspect baseline fit quality
- Visualize transaction timing

---

## Common Error Messages

### `GreptimeConnectionError: Failed to connect`

**Meaning**: Cannot reach GreptimeDB server

**Fix**: See [Connection Issues](#connection-issues) → "Failed to connect to GreptimeDB"

---

### `SettingsError: Invalid value for safety_factor.c: must be in (0, 1]`

**Meaning**: Safety factor configured incorrectly

**Fix**:
```yaml
orchestrator:
  safety_factor:
    c: 0.5  # Must be > 0 and ≤ 1
```

---

### `TokenRegistryError: Token not found in registry: djed`

**Meaning**: Token registry missing entry for asset (required for Minswap)

**Fix**: Add entry to `config/token_registry.csv`:
```csv
asset,policy_id,token_name_hex
djed,8db269c3ec630e06ae29f74bc39edd1f87c819f1056206e879a1cd61,446a656432
```

Or disable Minswap price source.

---

### `ValueError: Polynomial order must be non-negative`

**Meaning**: Invalid polynomial order in config

**Fix**:
```yaml
orchestrator:
  decision_gate:
    polynomial_order: 2  # Must be ≥ 0 (typical: 2-4)
```

---

### `KeyError: 'asset'` (in logs)

**Meaning**: Asset resolution failed, code trying to access missing key

**Fix**: Enable fallback or ensure all configured assets exist in database

---

### `MatplotlibError: Cannot connect to display`

**Meaning**: Chart generation failing (headless environment without X11)

**Fix**:
```bash
# Set matplotlib backend to non-interactive
export MPLBACKEND=Agg
python -m src.main
```

Or add to code:
```python
import matplotlib
matplotlib.use('Agg')
```

---

## Getting Help

If this guide doesn't solve your issue:

1. **Check logs with debug level**:
   ```bash
   python -m src.main --log-level DEBUG 2>&1 | tee debug.log
   ```

2. **Inspect metrics**:
   ```bash
   curl http://localhost:9808/metrics > metrics.txt
   ```

3. **Test components individually**:
   ```python
   # Test GreptimeDB connection
   from src.shared.greptime_reader import GreptimeReader
   from src.shared.config import GreptimeConnConfig
   
   config = GreptimeConnConfig(host="http://localhost", port=4000, database="liqwid", timeout=10)
   reader = GreptimeReader(config)
   print(reader.test_connection())
   ```

4. **Check database directly**:
   ```bash
   # Query GreptimeDB via HTTP
   curl -X POST http://localhost:4000/v1/sql \
     -d "sql=SHOW TABLES" \
     -d "db=liqwid"
   ```

5. **Review documentation**:
   - [README.md](README.md) - Quick start and overview
   - [ARCHITECTURE.md](ARCHITECTURE.md) - System design and data flows
   - [CONFIGURATION.md](CONFIGURATION.md) - Configuration reference

6. **Report issue**: Include debug logs, configuration (sanitized), and error messages

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)
