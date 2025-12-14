# Changelog

All notable changes to the Alert Orchestrator will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2025-01-21

### Major Release: Phase B - Residual Gating

This release introduces advanced residual-based decision gating to prevent withdrawals during anomalous market conditions.

### Added

- **Residual Gating System**:
  - Statistical anomaly detection using baseline fitting (polynomial or median)
  - Configurable thresholds (k-sigma or percentile-based)
  - Per-asset gate configuration with whitelist support
  - Threshold modes: `stddev` (k*σ) and `percentile` (central confidence interval)
  - Lookback window configuration for historical data analysis
  - Option to exclude latest point from sigma calculation
  - Fallback mode support (applies gate even when using date_range fallback)

- **Enhanced Diagnostics**:
  - Three-panel diagnostic charts (position view, residual plot, KDE distribution)
  - Sigma bands (±σ) and k-sigma threshold bands (±k*σ) visualization
  - Gaussian KDE for residual distribution analysis
  - Rate view support (USD rate and ADA rate time series)
  - Per-asset chart generation with configurable output directory

- **Prometheus Metrics Expansion**:
  - `wo_residual_usd`: Residual value at latest evaluation
  - `wo_sigma_usd`: Standard deviation of residuals
  - `wo_k_sigma`: K-sigma threshold value
  - `wo_residual_trigger`: Binary flag (0=not triggered, 1=triggered)
  - `wo_rate_usd`: USD rate time series metrics
  - `wo_rate_ada`: ADA rate time series metrics
  - Configurable metric exposure via `telemetry.expose` settings

- **Dashboard Enhancements**:
  - Residual gating status display with threshold information
  - Visual indicators for gate trigger status (✅/⚠️)
  - Chart view mode selector (position vs. rate)
  - Per-wallet W_max breakdown table with abbreviated addresses
  - Auto-refresh functionality (10-second interval)
  - Price comparison section with multi-source display

- **Configuration System**:
  - New `decision_gate` configuration block with 10+ parameters
  - Per-asset decision gate overrides
  - `diagnostics` configuration block for chart generation
  - `output_cleanup` configuration for automated file cleanup
  - Token registry validation at startup
  - Config doctor tool (`--print-config-normalized`)

- **API Endpoints**:
  - `GET /api/assets`: List of monitored assets
  - `GET /api/decisions`: Current decisions with full context
  - `GET /api/config/normalized`: Normalized configuration (auth required)
  - `POST /api/sync/transactions`: Manual transaction sync trigger (auth required)

- **Per-Wallet Calculations**:
  - Individual W_max computation per wallet based on wallet-specific gains
  - `WalletBreakdown` data model with abbreviated address display
  - Dashboard table showing per-wallet breakdown

- **Security Features**:
  - Basic authentication for sensitive endpoints
  - Configurable password hashing (sha256/bcrypt support)
  - `auth` configuration block with enable/disable toggle

- **CLI Enhancements**:
  - `--test-prefix` flag for safe testing with test_* tables
  - `--print-config-normalized` for configuration validation
  - `--log-level` for runtime log level control

### Changed

- **Decision Logic**:
  - Total W_max now computed as sum of per-wallet W_max values
  - Safety factor applied per-wallet instead of globally
  - Reference point fallback mode now configurable (`"data_range"` or `"null"`)
  - Evaluation skips assets with resolution errors (instead of failing entire run)

- **Chart Generation**:
  - Moved from client to orchestrator (consolidated diagnostic plotting)
  - Enhanced with residual analysis panels
  - Rate view support (conditional data loading based on view mode)
  - Fixed y-axis labels for rate views (shows "Rate" instead of "Position")

- **Configuration Structure**:
  - `telemetry.expose` now controls individual metric visibility
  - `diagnostics` section moved to orchestrator-level (from client)
  - `reference_keyword_fallback` added to orchestrator config

- **Dashboard UI**:
  - Redesigned decision status card with ref_mode display
  - Added per-wallet breakdown section
  - Improved chart embedding with view mode support
  - Better error state handling

### Fixed

- **Rate View Bug** (Commit d24fbe5):
  - Fixed rate view showing 0 values (conditional data loading implemented)
  - Fixed y-axis labels showing "Position (USD)" in rate mode (now shows "Rate")
  - Phase 1: Conditional data loading based on view mode
  - Phase 2: Proper y-axis labeling for rate views

- **Configuration Validation**:
  - Added validation for safety_factor.c (must be in (0, 1])
  - Added validation for polynomial_order (must be non-negative)
  - Added validation for assets list (must be non-empty)

- **Asset Resolution**:
  - Improved error handling for unresolvable assets
  - Resolution errors now reported per-asset (not fatal)

### Deprecated

- **Legacy Configuration**:
  - `diagnostics.only_whitelist_assets` (ignored, decision_gate.whitelist used instead)

### Removed

None

### Security

- Added HTTP Basic Authentication for sensitive endpoints
- Restricted config exposure to authenticated users only
- Added password hashing support (sha256/bcrypt)

---

## [1.0.0] - 2025-01-15

### Initial Release: Phase A - Core Withdrawal Advisor

First production-ready release of the Alert Orchestrator.

### Added

- **Core Decision Logic**:
  - Per-asset withdrawal recommendation calculation
  - Corrected gain computation accounting for deposits/withdrawals
  - Safety factor-based W_max calculation
  - Reference point identification from tagged withdrawals

- **Data Access Layer**:
  - GreptimeDB integration for position and transaction data
  - SQL query interface for time-series data
  - Transaction fetching from deposits/withdrawals tables
  - Asset resolution from display names to symbols

- **Metrics & Monitoring**:
  - Prometheus metrics export via `/metrics` endpoint
  - Core metrics: decision, wmax_usd, g_usd, v_ref_usd, v_t1_usd
  - Timestamp telemetry (t0, t1)
  - Dashboard at `/dashboard` with basic asset status

- **Configuration System**:
  - YAML-based configuration
  - `client` section for data access settings
  - `orchestrator` section for service settings
  - Reference keyword configuration
  - Safety factor configuration
  - Schedule configuration (evaluation interval)
  - Telemetry configuration (metrics server)

- **Price Sources**:
  - Liqwid GraphQL API integration
  - Minswap Aggregator API integration
  - GreptimeDB-cached price fallback
  - Price source priority configuration

- **Shared Modules**:
  - `greptime_reader`: Database query interface
  - `greptime_writer`: Database write interface
  - `liqwid_client`: Liqwid API client
  - `correct_calculations`: Gain calculation logic
  - `resolver`: Asset resolution
  - `models`: Data structures (AssetTimeSeries, Transaction)

- **CLI**:
  - `--config`: Custom config file path
  - `--once`: Single evaluation mode
  - `--no-telemetry`: Disable metrics server
  - `--log-level`: Log level control

- **Evaluation Loop**:
  - Periodic evaluation on configurable schedule
  - Stateless evaluation (fresh data fetch each iteration)
  - Per-asset decision computation
  - Metrics update after each evaluation

### Configuration

Minimal configuration example:
```yaml
client:
  greptime:
    host: "http://localhost"
    port: 4000
    database: "liqwid"
  assets: ["djed", "usdm"]

orchestrator:
  reference_keyword: "alert_driven_withdrawal"
  safety_factor:
    c: 0.5
  schedule:
    interval_minutes: 60
  telemetry:
    enabled: true
    listen_port: 9808
```

---

## [Unreleased]

### Planned Features

- **Phase C: Enhanced Price Comparison**:
  - Multi-source price comparison with delta metrics
  - Price mismatch alerting
  - Price source health monitoring

- **Phase D: Transaction Sync**:
  - Automated transaction syncing from Liqwid API
  - Wallet discovery
  - Duplicate detection and deduplication

- **Phase E: Advanced Analytics**:
  - Historical W_max trends
  - Gain attribution analysis
  - Wallet performance ranking

---

## Version History Summary

| Version | Release Date | Highlights |
|---------|--------------|------------|
| 2.0.0   | 2025-01-21   | Residual gating, per-wallet W_max, enhanced diagnostics |
| 1.0.0   | 2025-01-15   | Initial release, core withdrawal advisor functionality |

---

## Upgrade Guide

### From 1.0.0 to 2.0.0

**Breaking Changes**: None (backward compatible)

**New Configuration** (optional):
```yaml
orchestrator:
  # New: Residual gating
  decision_gate:
    enabled: true
    whitelist: ["djed", "usdm"]
    k_sigma: 2.0
  
  # New: Diagnostics
  diagnostics:
    enabled: true
    dir: "output"
  
  # New: Output cleanup
  output_cleanup:
    enabled: true
    expire_before_relative: "7d"
```

**Metrics Changes**:
- Added: `wo_residual_usd`, `wo_sigma_usd`, `wo_k_sigma`, `wo_residual_trigger`
- Added: `wo_rate_usd`, `wo_rate_ada`
- Changed: `wo_wmax_usd` now sum of per-wallet values (behavior unchanged for single-wallet)

**Dashboard Changes**:
- New per-wallet breakdown table
- New residual gating status display
- New chart view mode selector

**Steps**:
1. Update code: `git pull` or download new release
2. Update dependencies: `pip install -r requirements.txt`
3. (Optional) Add new config sections to `orchestrator_config.yaml`
4. (Optional) Create `token_registry.csv` for Minswap support
5. Restart service: `sudo systemctl restart alert-orchestrator`
6. Verify: Check dashboard and metrics for new features

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:
- Reporting bugs
- Suggesting features
- Submitting pull requests
- Coding standards

---

## Support

- **Documentation**: See `docs/` directory
- **Issues**: [GitHub Issues](https://github.com/your-org/alert-orchestrator/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/alert-orchestrator/discussions)

---

**Maintained by**: [Your Team Name]  
**License**: [Your License]
