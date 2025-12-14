# Alert Orchestrator

A real-time cryptocurrency withdrawal advisor system that monitors Liqwid supply positions and provides intelligent withdrawal recommendations based on corrected gain calculations, residual analysis, and configurable safety factors.

## Overview

The Alert Orchestrator is a Python-based monitoring service that:

- **Monitors** cryptocurrency supply positions in real-time from GreptimeDB
- **Calculates** corrected gains accounting for deposits and withdrawals
- **Analyzes** position residuals using statistical methods (polynomial fitting, KDE)
- **Recommends** safe withdrawal amounts per wallet using configurable safety factors
- **Exposes** metrics via Prometheus for alerting and visualization
- **Provides** an interactive web dashboard for monitoring asset decisions
- **Syncs** transaction data from Liqwid API to GreptimeDB

## Key Features

### 🎯 Core Functionality
- **Per-Wallet Withdrawal Calculations**: Computes maximum safe withdrawal amounts (`W_max`) for each wallet based on individual gains
- **Corrected Gain Analysis**: Accounts for all deposits/withdrawals to calculate true PnL since reference point
- **Residual-Based Gating**: Uses statistical analysis to detect anomalies and prevent withdrawals during unusual market conditions
- **Multi-Asset Support**: Monitors multiple stablecoins (DJED, USDM, USDC, USDT) simultaneously

### 📊 Monitoring & Observability
- **Prometheus Metrics**: Exports decision status, Wmax values, gains, residuals, and price comparisons
- **Web Dashboard**: Interactive HTML dashboard at `http://localhost:9808/dashboard` with:
  - Real-time asset decision status
  - Per-wallet withdrawal breakdown
  - Diagnostic charts (position, residuals, KDE distributions)
  - Price comparison across multiple sources
- **Diagnostic Plots**: Generates detailed analysis charts saved to `output/` directory

### 🔐 Safety & Reliability
- **Safety Factor**: Configurable multiplier (0 < c ≤ 1) to ensure conservative withdrawal recommendations
- **Decision Gating**: Configurable residual thresholds to hold withdrawals during anomalies
- **Price Source Fallback**: Multiple price sources with priority-based selection (Liqwid GraphQL, Minswap Aggregator, GreptimeDB)
- **Test Mode**: Writes to separate `test_*` tables for safe testing

## Quick Start

### Prerequisites

- Python 3.9+
- GreptimeDB instance with Liqwid position data
- Access to Liqwid GraphQL API (optional, for real-time prices)
- Access to Minswap Aggregator API (optional, for price comparison)

### Installation

#### Option 1: Docker (Recommended for Production)

The easiest way to deploy, especially on Synology NAS or other Docker-capable systems.

```bash
# 1. Navigate to the orchestrator directory
cd alert_orchestrator

# 2. Deploy with Docker Compose (easiest)
docker-compose up -d

# 3. Access metrics and dashboard
# Metrics: http://localhost:9808/metrics
# Dashboard: http://localhost:9808/dashboard
```

**For Synology Container Manager**, see [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) for detailed instructions.

> **Note**: The Docker image supports both x86_64 and ARM64 architectures automatically.

#### Option 2: Local Python Installation

1. **Clone the repository** and navigate to the orchestrator directory:
   ```bash
   cd alert_orchestrator
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the service**:
   - Copy and edit `config/orchestrator_config.yaml`
   - Set your GreptimeDB connection details
   - Configure your assets (e.g., `["djed", "usdc", "usdt", "usdm"]`)
   - Set reference keyword for tagged withdrawals (e.g., `"alert_driven"`)

4. **Set up token registry** (for Minswap price sources):
   - Create `config/token_registry.csv` with columns: `asset,policy_id,token_name_hex`
   - Example:
     ```csv
     asset,policy_id,token_name_hex
     djed,8db269c3ec630e06ae29f74bc39edd1f87c819f1056206e879a1cd61,446a656432
     usdm,c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad,0014df105553444d
     ```

### Running the Orchestrator

#### Standard Mode (Metrics Server + Dashboard)

```bash
# Run with default config (config/orchestrator_config.yaml)
python -m src.main

# Run with custom config
python -m src.main --config /path/to/config.yaml

# Set log level
python -m src.main --log-level DEBUG
```

The service will:
- Start an HTTP server on port 9808 (configurable)
- Expose Prometheus metrics at `/metrics`
- Serve an interactive dashboard at `/dashboard`
- Run evaluations every N minutes (configurable via `schedule.interval_minutes`)

#### One-Shot Mode (Single Evaluation)

```bash
# Run once and exit (useful for testing)
python -m src.main --once

# Example output:
# djed: decision=1, wmax_usd=1234.56 (3 wallets)
# usdm: decision=0, wmax_usd=0.00 [reason: non-positive gains since t0 (clamped)]
```

#### Test Mode (Safe Testing)

```bash
# Use test_* tables for all writes (reads still use normal tables)
python -m src.main --test-prefix
```

#### Config Doctor (Validation)

```bash
# Print normalized configuration as JSON and exit
python -m src.main --print-config-normalized
```

### Accessing the Dashboard

Once running, open your browser to:
```
http://localhost:9808/dashboard
```

Features:
- **Asset Selection**: Dropdown to switch between monitored assets
- **Decision Status**: Visual indicators (✅ WITHDRAW_OK, ⏸️ HOLD, ❌ ERROR)
- **Per-Wallet Breakdown**: Table showing `W_max` for each wallet address
- **Diagnostic Charts**: Position view, residual analysis, KDE distribution
- **Price Comparison**: Real-time price differences across sources
- **Residual Gating**: Shows threshold status (σ, k*σ bands)

### Prometheus Metrics

Scrape metrics from:
```
http://localhost:9808/metrics
```

Key metrics (configurable via `orchestrator.telemetry.expose`):
- `wo_decision{asset,alternate_asset_name,ref_mode}` - Decision status (1=WITHDRAW_OK, 0=HOLD, -1=ERROR)
- `wo_wmax_usd{asset,alternate_asset_name,ref_mode}` - Maximum withdrawal amount (USD)
- `wo_g_usd{asset,alternate_asset_name,ref_mode}` - Corrected gain since t0 (USD)
- `wo_residual_usd{asset,alternate_asset_name,ref_mode}` - Residual at t1 (USD)
- `wo_sigma_usd{asset,alternate_asset_name,ref_mode}` - Residual standard deviation (USD)
- `wo_residual_trigger{asset,alternate_asset_name,ref_mode}` - Residual gate trigger (0/1)
- `wo_price_usd{asset,alternate_asset_name,source}` - Price by source (USD)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Alert Orchestrator                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐         ┌──────────────────┐                 │
│  │   main.py    │────────▶│  alert_logic.py  │                 │
│  │ (Entry Point)│         │ (Core Decision   │                 │
│  │              │         │     Logic)       │                 │
│  └──────┬───────┘         └────────┬─────────┘                 │
│         │                          │                            │
│         ▼                          ▼                            │
│  ┌──────────────┐         ┌──────────────────┐                 │
│  │  settings.py │         │ diagnostics.py   │                 │
│  │ (Config Mgmt)│         │ (Chart Gen)      │                 │
│  └──────────────┘         └──────────────────┘                 │
│         │                          │                            │
│         ▼                          │                            │
│  ┌──────────────────────────────────┴─────┐                    │
│  │          exporter.py                   │                    │
│  │   (HTTP Server, Metrics, Dashboard)    │                    │
│  └────────────────────────────────────────┘                    │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────────┐                  │
│  │   Shared Modules (greptime_reader,       │                  │
│  │   liqwid_client, models, resolver)       │                  │
│  └──────────────────────────────────────────┘                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
   ┌─────────────┐      ┌─────────────┐      ┌──────────────┐
   │ GreptimeDB  │      │  Liqwid API │      │ Minswap API  │
   │ (Positions, │      │  (Txs,      │      │ (Prices)     │
   │  Txs)       │      │   Prices)   │      │              │
   └─────────────┘      └─────────────┘      └──────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed component descriptions.

## Configuration

The orchestrator uses a YAML configuration file with two main sections:

### `client` Section
Shared configuration for data access (reused from the client):
- GreptimeDB connection settings
- Asset list (strictly enforced)
- Table prefixes for positions, deposits, withdrawals
- Date range for analysis
- Smoothing configuration (polynomial fitting, moving averages)

### `orchestrator` Section
Orchestrator-specific settings:
- **Reference keyword**: Tag for withdrawal events used as reference points (e.g., `"alert_driven_withdrawal"`)
- **Safety factor**: Multiplier `c` (0 < c ≤ 1) for conservative withdrawal limits
- **Schedule**: Evaluation interval in minutes
- **Telemetry**: Prometheus metrics server configuration
- **Decision gate**: Residual-based gating rules
- **Diagnostics**: Chart generation settings
- **APIs**: External API endpoints (Liqwid GraphQL, Minswap Aggregator)

See [CONFIGURATION.md](CONFIGURATION.md) for complete schema documentation.

## Common Use Cases

### 1. Monitor DJED Withdrawals

```yaml
# config/orchestrator_config.yaml
client:
  assets: ["djed"]
  
orchestrator:
  reference_keyword: "alert_driven_withdrawal"
  safety_factor:
    c: 0.5  # 50% safety margin
  decision_gate:
    enabled: true
    whitelist: ["djed"]
    k_sigma: 2.0  # Hold if residual > 2σ
```

Run:
```bash
python -m src.main
```

Check dashboard at `http://localhost:9808/dashboard?asset=djed`

### 2. Test Configuration Changes

```bash
# Validate config
python -m src.main --print-config-normalized | jq

# Run single evaluation
python -m src.main --once

# Run with test tables (safe)
python -m src.main --test-prefix --once
```

### 3. Monitor Multiple Assets with Per-Asset Settings

```yaml
client:
  assets: ["djed", "usdm", "wanusdc"]
  output:
    smoothing:
      default:
        polynomial_order: 2
      djed:
        polynomial_order: 3  # Higher order for DJED

orchestrator:
  decision_gate:
    enabled: true
    whitelist: ["djed", "usdm"]  # Only gate these assets
    per_asset:
      djed:
        k_sigma: 2.5
      usdm:
        k_sigma: 2.0
```

### 4. Sync Transactions from Liqwid API

Access the transaction sync endpoint:
```bash
curl -X POST http://localhost:9808/api/sync/transactions
```

Or use basic auth (if configured):
```bash
curl -X POST -u username:password http://localhost:9808/api/sync/transactions
```

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues and solutions.

### Quick Checks

**No decisions shown in dashboard?**
- Check `client.assets` is non-empty in config
- Verify GreptimeDB connection: check logs for connection errors
- Ensure position data exists for configured assets

**Residual gate always holding?**
- Check `orchestrator.decision_gate.k_sigma` threshold (default: 2.0)
- View diagnostic charts in `output/` to see residual values
- Adjust threshold or disable gate: `decision_gate.enabled: false`

**Wmax always 0?**
- Check for non-positive gains: view `g_usd` metric
- Verify reference point exists (tagged withdrawal with `reference_keyword`)
- Check safety factor: `safety_factor.c` (higher = more conservative)

**Dashboard not loading?**
- Check HTTP server started: look for "Starting HTTP server" in logs
- Verify port not in use: `lsof -i :9808`
- Check firewall settings

## Development

### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test module
pytest tests/test_alert_logic.py

# Run with coverage
pytest --cov=src tests/
```

### Code Structure

```
alert_orchestrator/
├── src/
│   ├── main.py                 # Entry point, CLI args
│   ├── core/                   # Core orchestrator logic
│   │   ├── alert_logic.py      # Decision evaluation
│   │   ├── exporter.py         # HTTP server, metrics, dashboard
│   │   ├── settings.py         # Configuration management
│   │   ├── diagnostics.py      # Chart generation
│   │   ├── transaction_syncer.py  # Liqwid API sync
│   │   ├── price_source.py     # Price source adapters
│   │   ├── reference_state.py  # Reference point logic
│   │   └── ...
│   └── shared/                 # Shared utilities
│       ├── greptime_reader.py  # GreptimeDB queries
│       ├── greptime_writer.py  # GreptimeDB writes
│       ├── liqwid_client.py    # Liqwid API client
│       ├── models.py           # Data models
│       ├── correct_calculations.py  # Gain calculations
│       └── ...
├── config/
│   ├── orchestrator_config.yaml  # Main config
│   └── token_registry.csv        # Token mappings
├── tests/                       # Unit tests
├── output/                      # Generated charts
└── docs/                        # Documentation
```

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for contributor guidelines.

## API Reference

### HTTP Endpoints

- `GET /metrics` - Prometheus metrics (OpenMetrics format)
- `GET /dashboard` - Interactive web dashboard
- `GET /api/assets` - List of monitored assets (JSON)
- `GET /api/decisions` - Current decisions for all assets (JSON)
- `GET /api/config/normalized` - Normalized configuration (JSON, auth required)
- `POST /api/sync/transactions` - Trigger transaction sync (auth required)

### Dashboard Query Parameters

- `?asset=<name>` - Select specific asset (e.g., `?asset=djed`)
- `?view=<mode>` - Chart view mode: `position` (default) or `rate`

See [API.md](docs/API.md) for detailed endpoint documentation.

## Security Considerations

### Basic Authentication

Enable authentication for sensitive endpoints:

```yaml
orchestrator:
  auth:
    enabled: true
    username: "admin"
    password_hash: "sha256:<hash>"  # Use bcrypt in production
```

Protected endpoints:
- `GET /api/config/normalized` (config doctor)
- `POST /api/sync/transactions` (transaction sync)

### Network Security

- **Bind to localhost only** in production: `listen_address: "127.0.0.1"`
- **Use reverse proxy** (nginx, Caddy) with TLS for external access
- **Firewall rules**: Restrict access to port 9808
- **API authentication**: Enable for production deployments

## Performance

### Resource Usage

- **Memory**: ~50-200 MB (depends on data volume and assets monitored)
- **CPU**: Minimal (< 5% during evaluations)
- **Network**: Low bandwidth (periodic API calls every N minutes)
- **Disk**: Diagnostic charts consume ~1-5 MB per asset per evaluation

### Optimization Tips

- **Reduce evaluation frequency**: Increase `schedule.interval_minutes` (e.g., 60 minutes)
- **Limit lookback period**: Set `decision_gate.lookback_hours` (e.g., 48 hours)
- **Disable charts**: Set `diagnostics.enabled: false` if not needed
- **Reduce metric exposure**: Disable unneeded metrics in `telemetry.expose`

## License

[Your License Here]

## Support

- **Documentation**: See `docs/` directory for detailed guides
- **Issues**: Report bugs and feature requests via GitHub Issues
- **Community**: [Your community channels]

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and release notes.

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)
