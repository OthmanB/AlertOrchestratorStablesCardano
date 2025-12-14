# Getting Started with Alert Orchestrator

This guide will walk you through setting up and running the Alert Orchestrator from scratch.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [First Run](#first-run)
5. [Understanding the Dashboard](#understanding-the-dashboard)
6. [Setting Up Monitoring](#setting-up-monitoring)
7. [Next Steps](#next-steps)

---

## Prerequisites

Before you begin, ensure you have:

### Required

1. **Python 3.9 or higher**
   ```bash
   python --version
   # Should output: Python 3.9.x or higher
   ```

2. **GreptimeDB instance** with Liqwid position data
   - Running and accessible via HTTP
   - Database name (e.g., `liqwid`)
   - Host/port (e.g., `localhost:4000`)
   - Tables: `liqwid_supply_positions_<asset>`, `liqwid_deposits_<asset>`, `liqwid_withdrawals_<asset>`

3. **Network access** to:
   - GreptimeDB server
   - Liqwid GraphQL API (optional, for real-time prices)
   - Minswap Aggregator API (optional, for price comparison)

### Optional (Recommended)

1. **Git** (for cloning repository)
2. **Virtual environment tool** (venv, conda, virtualenv)
3. **Grafana** (for visualizing Prometheus metrics)
4. **curl or httpie** (for testing API endpoints)

---

## Installation

### Step 1: Get the Code

If you have Git:
```bash
git clone <repository-url>
cd alert_orchestrator
```

Or download and extract the source code:
```bash
cd /path/to/alert_orchestrator
```

### Step 2: Create Virtual Environment (Recommended)

**Using venv** (built-in):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

**Using conda**:
```bash
conda create -n orchestrator python=3.9
conda activate orchestrator
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

**Expected packages**:
- `requests` - HTTP client for API calls and GreptimeDB
- `pyyaml` - YAML configuration parsing
- `numpy` - Numerical computations
- `matplotlib` - Chart generation
- `prometheus_client` - Metrics export
- `python-dateutil` - Date/time utilities

**Verify installation**:
```bash
python -c "import requests, yaml, numpy, matplotlib, prometheus_client; print('All dependencies installed!')"
```

---

## Configuration

### Step 1: Create Configuration File

Copy the example configuration:
```bash
cp config/orchestrator_config.yaml.example config/orchestrator_config.yaml
```

Or create from scratch:
```bash
# Create config directory if not exists
mkdir -p config

# Create config file
cat > config/orchestrator_config.yaml << 'EOF'
client:
  greptime:
    host: "http://localhost"
    port: 4000
    database: "liqwid"
    timeout: 10
  
  assets: ["djed", "usdm", "wanusdc", "wanusdt"]
  
  table_asset_prefix: "liqwid_supply_positions_"
  deposits_prefix: "liqwid_deposits_"
  withdrawals_prefix: "liqwid_withdrawals_"
  
  date_range:
    start: null
    end: null
  
  output:
    smoothing:
      default:
        window_type: "polynomial"
        window_size_hours: 24.0
        polynomial_order: 2

orchestrator:
  reference_keyword: "alert_driven_withdrawal"
  reference_keyword_fallback: "data_range"
  
  safety_factor:
    c: 0.5
  
  timezone: "UTC"
  
  schedule:
    interval_minutes: 60
  
  telemetry:
    enabled: true
    listen_address: "0.0.0.0"
    listen_port: 9808
    path: "/metrics"
    metric_prefix: "wo_"
    
    expose:
      decision: true
      wmax_usd: true
      v_ref_usd: true
      v_t1_usd: true
      g_usd: true
      residual_usd: true
      sigma_usd: true
      residual_trigger: true
  
  decision_gate:
    enabled: true
    whitelist: ["djed", "usdm"]
    basis: "corrected_position"
    method: "polynomial_fit"
    polynomial_order: 2
    k_sigma: 2.0
    min_points: 10
    lookback_hours: 48.0
  
  diagnostics:
    enabled: true
    dir: "output"
    include_sigma_band: true
    include_k_sigma_band: true
EOF
```

### Step 2: Edit Configuration

Edit `config/orchestrator_config.yaml` with your settings:

#### GreptimeDB Connection

```yaml
client:
  greptime:
    host: "http://YOUR_GREPTIME_HOST"  # e.g., "http://localhost" or "http://192.168.1.100"
    port: 4000                           # Default GreptimeDB HTTP port
    database: "YOUR_DATABASE_NAME"       # e.g., "liqwid"
    timeout: 10                          # Request timeout in seconds
```

#### Assets to Monitor

```yaml
client:
  assets:
    - "djed"       # Djed stablecoin
    - "usdm"       # USDM stablecoin
    - "wanusdc"    # Wrapped USDC
    - "wanusdt"    # Wrapped USDT
```

**Important**: Asset names must match GreptimeDB table suffixes. For example:
- `"djed"` → queries `liqwid_supply_positions_djed`
- `"wanusdc"` → queries `liqwid_supply_positions_wanusdc`

#### Reference Keyword

This is the memo/tag used to identify reference withdrawal events:

```yaml
orchestrator:
  reference_keyword: "alert_driven_withdrawal"  # Tag used in withdrawal memos
  reference_keyword_fallback: "data_range"      # Fallback if no tagged withdrawal found
```

**How it works**:
- The orchestrator looks for withdrawals with `memo = "alert_driven_withdrawal"`
- The most recent tagged withdrawal becomes the reference point $(t_0, V(t_0))$
- If no tagged withdrawal found, uses `date_range.start` as $t_0$ (if fallback = "data_range")

#### Safety Factor

Controls how conservative withdrawal recommendations are:

```yaml
orchestrator:
  safety_factor:
    c: 0.5  # 50% of gains can be withdrawn (0 < c ≤ 1)
```

**Examples**:
- `c: 0.5` (default) - Can withdraw 50% of gains
- `c: 1.0` - Can withdraw 100% of gains (aggressive)
- `c: 0.25` - Can withdraw 25% of gains (very conservative)

#### Evaluation Schedule

```yaml
orchestrator:
  schedule:
    interval_minutes: 60  # Run evaluation every 60 minutes
```

**Recommendations**:
- `60` - Hourly evaluations (default, good for most cases)
- `30` - Every 30 minutes (more frequent monitoring)
- `120` - Every 2 hours (less load on database)

### Step 3: Set Up Token Registry (Optional)

Only required if using Minswap price sources for price comparison.

Create `config/token_registry.csv`:
```csv
asset,policy_id,token_name_hex
djed,8db269c3ec630e06ae29f74bc39edd1f87c819f1056206e879a1cd61,446a656432
usdm,c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad,0014df105553444d
```

**How to find policy ID and token name**:
1. Go to [Minswap token page](https://app.minswap.org/tokens)
2. Search for your asset (e.g., "DJED")
3. Click on token details
4. Copy "Policy ID" and "Token Name (Hex)"

Or skip this if not using Minswap:
```yaml
orchestrator:
  apis:
    liqwid_graphql: "https://api.liqwid.finance/graphql"
    # Don't configure minswap_aggregator
```

### Step 4: Validate Configuration

Check configuration is valid:
```bash
python -m src.main --print-config-normalized
```

This should output JSON with your configuration. Check for:
- No error messages
- All required fields present
- Values look correct

---

## First Run

### Step 1: Test GreptimeDB Connection

Before running the orchestrator, test database connectivity:

```bash
# Test connection using curl
curl -X POST http://localhost:4000/v1/sql \
  -d "sql=SHOW TABLES LIKE 'liqwid_supply_positions_%'" \
  -d "db=liqwid"
```

Expected output: JSON with list of tables

### Step 2: Run One-Shot Mode

Run a single evaluation to verify everything works:

```bash
python -m src.main --once --log-level INFO
```

**Expected output**:
```
INFO - Loaded configuration from config/orchestrator_config.yaml
INFO - Initialized GreptimeDB reader: http://localhost:4000
INFO - Token registry validated for assets: djed, usdm, wanusdc, wanusdt
djed: decision=1, wmax_usd=1234.56 (3 wallets)
usdm: decision=0, wmax_usd=0.00 [reason: non-positive gains since t0 (clamped)]
wanusdc: decision=1, wmax_usd=567.89 (2 wallets)
wanusdt: decision=0, wmax_usd=0.00 [reason: residual gate triggered]
```

**Understanding the output**:
- `decision=1`: Withdrawals OK, W_max > 0
- `decision=0`: HOLD, W_max = 0 (check reason in brackets)
- `decision=-1`: ERROR (check logs for details)
- `wmax_usd`: Total maximum withdrawal amount in USD
- `(N wallets)`: Number of wallets with positions

### Step 3: Check Diagnostic Charts (Optional)

If diagnostics enabled, charts will be in `output/` directory:

```bash
ls -lh output/
# Should show files like: djed_20250121_143022_residual_composite.png
```

Open a chart to visualize:
- Position view with smoothed baseline
- Residual plot with threshold bands
- KDE distribution of residuals

### Step 4: Start in Server Mode

Run the orchestrator with metrics server and dashboard:

```bash
python -m src.main --log-level INFO
```

**Expected output**:
```
INFO - Loaded configuration from config/orchestrator_config.yaml
INFO - Starting HTTP server on 0.0.0.0:9808
INFO - Prometheus metrics available at http://0.0.0.0:9808/metrics
INFO - Dashboard available at http://0.0.0.0:9808/dashboard
INFO - Running evaluation loop every 60 minutes
INFO - First evaluation starting...
```

The server will run continuously, performing evaluations every N minutes.

**To stop**: Press `Ctrl+C`

---

## Understanding the Dashboard

### Accessing the Dashboard

Open your browser to:
```
http://localhost:9808/dashboard
```

Or from another machine:
```
http://<server-ip>:9808/dashboard
```

### Dashboard Sections

#### 1. Asset Selection

**Dropdown menu** at top: Select which asset to view

**Features**:
- Alphabetically sorted
- Shows all configured assets
- Updates URL parameter: `?asset=djed`

#### 2. Decision Status Card

**Shows**:
- Decision status badge:
  - 🟢 **WITHDRAW_OK** (decision = 1) - Green
  - 🟡 **HOLD** (decision = 0) - Orange
  - 🔴 **ERROR** (decision = -1) - Red
- Reference mode: "keyword" or "data_range" or "null"
- Total W_max (sum across all wallets)
- Number of wallets

**Example**:
```
✅ WITHDRAW_OK (keyword)
Total W_max: $1,234.56 (3 wallets)
```

#### 3. Per-Wallet Breakdown Table

**Columns**:
- **Wallet Address**: Abbreviated address (first 8 + last 4 chars)
- **W_max (USD)**: Maximum withdrawal amount for this wallet

**Example**:
```
Wallet Address          W_max (USD)
addr1qxe...abc123       $456.78
addr1qyf...def456       $389.12
addr1qzg...ghi789       $388.66
```

**Interpretation**:
- Each wallet has independent W_max based on their gains
- Sum of all rows = Total W_max shown in status card
- Negative gains clamped to $0.00

#### 4. Diagnostic Chart

**Three-panel chart** showing:

**Top Panel - Position View**:
- Blue line: Raw position value over time
- Orange dashed: Smoothed baseline (polynomial fit)
- Green triangles (▲): Deposits
- Red triangles (▼): Withdrawals
- Shaded bands: $\pm \sigma$ and $\pm k \cdot \sigma$ (if enabled)

**Middle Panel - Residual Plot**:
- Blue dots: Residuals (deviations from baseline)
- Red dot: Latest residual
- Orange dashed: $\pm \sigma$ bands
- Red dashed: $\pm k \cdot \sigma$ threshold
- Interpretation: If red dot outside red dashed lines, gate triggered

**Bottom Panel - KDE Distribution**:
- Curve: Probability density of residuals
- Red vertical line: Latest residual position
- Orange/red vertical lines: Threshold markers
- Interpretation: Shows how unusual the latest residual is

#### 5. Residual Gating Section

**Shows**:
- Residual value at t1 (latest)
- Sigma (σ) - standard deviation of residuals
- k*σ - threshold value
- Trigger status:
  - ✅ **NOT TRIGGERED** - Gate allows withdrawals
  - ⚠️ **TRIGGERED** - Gate holding withdrawals

**Example**:
```
Residual (t1): $123.45
Sigma: $50.00
k*σ Threshold: $100.00 (k=2.0)
Status: ⚠️ TRIGGERED (|residual| > threshold)
```

#### 6. Price Comparison Section (Optional)

Shows prices from multiple sources:

**Table**:
```
Source              Price (USD)    Delta (Abs)    Delta (%)
liqwid_graphql      $1.0023        -              -
minswap_aggregator  $1.0019        $0.0004        0.04%
greptime(liqwid)    $1.0020        $0.0003        0.03%
```

**Mismatch Flag**: Shows if price difference exceeds threshold

#### 7. Auto-Refresh

Dashboard auto-refreshes every 10 seconds to show latest data.

**Disable**: Add `?autorefresh=false` to URL

---

## Setting Up Monitoring

### Prometheus Scraping

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'alert_orchestrator'
    static_configs:
      - targets: ['localhost:9808']
    metrics_path: '/metrics'
    scrape_interval: 60s
```

Restart Prometheus:
```bash
sudo systemctl restart prometheus
```

Verify scraping:
```bash
curl http://localhost:9090/api/v1/targets | jq
# Look for "alert_orchestrator" job
```

### Grafana Dashboard

#### 1. Add Prometheus Data Source

In Grafana:
1. Go to **Configuration** → **Data Sources**
2. Click **Add data source**
3. Select **Prometheus**
4. URL: `http://localhost:9090` (or your Prometheus URL)
5. Click **Save & Test**

#### 2. Import Dashboard Template

Create new dashboard with panels:

**Panel 1 - Decision Status**:
- Query: `wo_decision{asset="djed"}`
- Visualization: Gauge
- Thresholds: -1 (red), 0 (orange), 1 (green)

**Panel 2 - W_max Value**:
- Query: `wo_wmax_usd{asset="djed"}`
- Visualization: Graph (time series)
- Unit: USD

**Panel 3 - Corrected Gains**:
- Query: `wo_g_usd{asset="djed"}`
- Visualization: Graph
- Unit: USD

**Panel 4 - Residual Gating**:
- Queries:
  - `wo_residual_usd{asset="djed"}` (Residual)
  - `wo_sigma_usd{asset="djej"}` (Sigma)
  - `wo_sigma_usd{asset="djed"} * wo_k_sigma{asset="djed"}` (Threshold)
- Visualization: Graph with threshold lines

**Panel 5 - Residual Trigger**:
- Query: `wo_residual_trigger{asset="djed"}`
- Visualization: Stat
- Values: 0 (OK), 1 (TRIGGERED)
- Thresholds: 0 (green), 1 (red)

#### 3. Set Up Alerts

Create alert rule in Grafana:

**Alert: Residual Gate Triggered**
- Condition: `wo_residual_trigger{asset="djed"} == 1`
- For: 5 minutes
- Action: Send notification

**Alert: W_max Dropped to Zero**
- Condition: `wo_wmax_usd{asset="djed"} == 0 AND wo_decision{asset="djed"} == 1`
- For: 10 minutes
- Action: Send notification

### Alertmanager Integration (Optional)

Configure Alertmanager for notifications:

```yaml
# alertmanager.yml
route:
  group_by: ['alertname', 'asset']
  receiver: 'slack'

receivers:
  - name: 'slack'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL'
        channel: '#alerts'
        text: 'Alert Orchestrator Alert: {{ .CommonAnnotations.description }}'
```

---

## Next Steps

### 1. Tune Configuration

Based on your first runs, adjust:

**Residual Gate Sensitivity**:
```yaml
orchestrator:
  decision_gate:
    k_sigma: 2.5  # Increase if too many false positives
```

**Safety Factor**:
```yaml
orchestrator:
  safety_factor:
    c: 0.7  # Adjust based on risk tolerance
```

**Evaluation Frequency**:
```yaml
orchestrator:
  schedule:
    interval_minutes: 30  # More frequent monitoring
```

### 2. Set Up Production Deployment

**Using systemd** (Linux):

Create service file `/etc/systemd/system/alert-orchestrator.service`:
```ini
[Unit]
Description=Alert Orchestrator
After=network.target

[Service]
Type=simple
User=orchestrator
WorkingDirectory=/opt/alert_orchestrator
Environment="PATH=/opt/alert_orchestrator/venv/bin"
ExecStart=/opt/alert_orchestrator/venv/bin/python -m src.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable alert-orchestrator
sudo systemctl start alert-orchestrator
sudo systemctl status alert-orchestrator
```

**Using Docker** (if available):

Create `Dockerfile`:
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "-m", "src.main"]
```

Build and run:
```bash
docker build -t alert-orchestrator .
docker run -d -p 9808:9808 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/output:/app/output \
  --name orchestrator \
  alert-orchestrator
```

### 3. Enable Advanced Features

**Transaction Sync** (optional):
```yaml
orchestrator:
  transaction_sync:
    enabled: true
  
  apis:
    liqwid_graphql: "https://api.liqwid.finance/graphql"
```

Trigger sync:
```bash
curl -X POST http://localhost:9808/api/sync/transactions
```

**Basic Authentication**:
```yaml
orchestrator:
  auth:
    enabled: true
    username: "admin"
    password_hash: "sha256:<hash>"  # Use bcrypt in production
```

**Per-Asset Decision Gate Settings**:
```yaml
orchestrator:
  decision_gate:
    per_asset:
      djed:
        k_sigma: 2.5
        polynomial_order: 3
      usdm:
        k_sigma: 2.0
        polynomial_order: 2
```

### 4. Explore Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Deep dive into system design
- [CONFIGURATION.md](CONFIGURATION.md) - Complete configuration reference
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues and solutions
- [API.md](docs/API.md) - HTTP API documentation

### 5. Monitor and Maintain

**Daily**:
- Check dashboard for decision status
- Verify evaluations running (check `wo_last_eval_timestamp_seconds`)

**Weekly**:
- Review diagnostic charts for trends
- Check Grafana alerts
- Clean up old output files (automatic if `output_cleanup` enabled)

**Monthly**:
- Review configuration (safety factor, gate thresholds)
- Analyze false positive rate for residual gate
- Update token registry (if assets added)

---

## Troubleshooting Common Setup Issues

### Issue: "Failed to connect to GreptimeDB"

**Check**:
```bash
curl -X POST http://localhost:4000/v1/sql -d "sql=SELECT 1" -d "db=liqwid"
```

**Fix**: Verify GreptimeDB is running and accessible

---

### Issue: "Assets list is empty"

**Fix**: Add assets to configuration:
```yaml
client:
  assets: ["djed", "usdm"]  # Must be non-empty
```

---

### Issue: "No position data available"

**Check**:
```sql
SELECT COUNT(*) FROM liqwid_supply_positions_djed;
```

**Fix**: Ensure data ingestion is working, or adjust `date_range` in config

---

### Issue: "Dashboard not loading"

**Check**:
```bash
curl http://localhost:9808/dashboard
```

**Fix**: Ensure telemetry enabled in config:
```yaml
orchestrator:
  telemetry:
    enabled: true
```

---

For more troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

**Congratulations!** You've successfully set up the Alert Orchestrator. 🎉

**Next**: Explore the dashboard, set up Grafana monitoring, and tune configuration for your use case.

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)
