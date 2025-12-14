# Alert Orchestrator Architecture

This document provides a comprehensive overview of the Alert Orchestrator's architecture, including component descriptions, data flows, and design decisions.

## Table of Contents

1. [System Overview](#system-overview)
2. [Core Components](#core-components)
3. [Data Flow](#data-flow)
4. [Decision Logic](#decision-logic)
5. [Price Sources](#price-sources)
6. [Residual Gating](#residual-gating)
7. [Configuration System](#configuration-system)
8. [Database Schema](#database-schema)
9. [External Dependencies](#external-dependencies)
10. [Design Decisions](#design-decisions)

---

## System Overview

The Alert Orchestrator is a **monitoring service** that periodically evaluates cryptocurrency supply positions and provides withdrawal recommendations. It does **not** execute withdrawals automatically - it only provides metrics and dashboard insights for manual decision-making.

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Alert Orchestrator                            │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                      Evaluation Loop                         │    │
│  │  (Runs every N minutes, controlled by schedule.interval)     │    │
│  └──┬────────────────────────────────────────────────────────┬──┘    │
│     │                                                         │       │
│     ▼                                                         ▼       │
│  ┌──────────────────────┐                         ┌─────────────────┐│
│  │   alert_logic.py     │                         │  exporter.py    ││
│  │  evaluate_once()     │────────────────────────▶│  (Metrics &     ││
│  │                      │    AssetDecision dict   │   Dashboard)    ││
│  │  • Resolve assets    │                         │                 ││
│  │  • Get reference pts │                         │  • HTTP server  ││
│  │  • Fetch positions   │                         │  • Prometheus   ││
│  │  • Calculate gains   │                         │  • Web UI       ││
│  │  • Apply gate logic  │                         │  • API endpoints││
│  │  • Compute Wmax      │                         └─────────────────┘│
│  └──────┬───────────────┘                                            │
│         │                                                             │
│         ▼                                                             │
│  ┌──────────────────────────────────────────────────────────┐        │
│  │              Shared Modules & Utilities                  │        │
│  │                                                           │        │
│  │  • greptime_reader (queries)                             │        │
│  │  • greptime_writer (writes)                              │        │
│  │  • liqwid_client (API client)                            │        │
│  │  • correct_calculations (gain logic)                     │        │
│  │  • resolver (asset resolution)                           │        │
│  │  • models (data structures)                              │        │
│  └───────────────────────────────────────────────────────────┘        │
│                                                                        │
└────────────────────────────────────────┬───────────────────────────────┘
                                         │
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
            ┌──────────────┐     ┌──────────────┐    ┌──────────────┐
            │ GreptimeDB   │     │  Liqwid API  │    │ Minswap API  │
            │              │     │              │    │              │
            │ • Positions  │     │ • Txs        │    │ • Prices     │
            │ • Txs        │     │ • Prices     │    │              │
            │ • Prices     │     │ • Wallets    │    │              │
            └──────────────┘     └──────────────┘    └──────────────┘
```

### Key Characteristics

- **Stateless**: No persistent state between evaluations (all data fetched fresh)
- **Periodic**: Runs on configurable schedule (default: every 60 minutes)
- **Read-Only Decision Logic**: Reads positions, computes recommendations, exposes metrics
- **Write Operations**: Only writes to GreptimeDB during transaction sync (optional)
- **Thread-Safe**: Uses single evaluation thread with HTTP server on separate threads

---

## Core Components

### 1. Entry Point (`main.py`)

**Purpose**: Application initialization, CLI argument parsing, and evaluation orchestration.

**Key Functions**:
- `main(config_path, once, no_telemetry, log_level, print_config_normalized, test_prefix)`: Entry point
  - Loads settings from YAML
  - Validates token registry
  - Starts HTTP server (if telemetry enabled)
  - Launches evaluation loop (unless `--once` mode)
  
- `_evaluation_loop(settings, exporter)`: Infinite loop
  - Sleeps for `interval_minutes` between evaluations
  - Calls `evaluate_once()` from `alert_logic`
  - Updates metrics in `exporter`

**CLI Flags**:
- `--config <path>`: Custom config file path
- `--once`: Single evaluation then exit
- `--no-telemetry`: Disable metrics server
- `--log-level <level>`: Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `--print-config-normalized`: Config doctor (prints normalized config as JSON, exits)
- `--test-prefix`: Test mode (uses `test_*` tables for writes)

**Startup Sequence**:
1. Parse CLI arguments
2. Setup colored logging
3. Load settings from YAML
4. Validate token registry (if Minswap price sources configured)
5. Run startup housekeeping (delete old output files)
6. Start HTTP server (if telemetry enabled and not `--once`)
7. Launch evaluation loop or run once

---

### 2. Decision Logic (`alert_logic.py`)

**Purpose**: Core evaluation logic that computes per-asset withdrawal recommendations.

**Main Function**: `evaluate_once(reader, settings) -> Dict[str, AssetDecision]`

**Evaluation Steps** (per asset):

1. **Asset Resolution**:
   - Converts display names (e.g., "USDT") to GreptimeDB symbols (e.g., "wanusdt")
   - Uses `Resolver` class with Liqwid API policy ID lookups
   - Stores resolution errors for display

2. **Reference Point Identification**:
   - Queries `liqwid_withdrawals_<asset>` table for tagged withdrawals matching `reference_keyword`
   - Extracts most recent tagged withdrawal as reference point $(t_0, V_i(t_0))$
   - Applies fallback if no tagged withdrawal found:
     - `"data_range"`: Uses `date_range.start` as $t_0$
     - `"null"`: Skips asset (decision = HOLD)

3. **Position & Transaction Data Fetching**:
   - Fetches position time series from `liqwid_supply_positions_<asset>` table
   - Fetches deposits from `liqwid_deposits_<asset>` table
   - Fetches withdrawals from `liqwid_withdrawals_<asset>` table
   - Date range: $(t_0, t_1)$ where $t_1$ is current time (UTC)

4. **Corrected Gain Calculation**:
   - Calls `calculate_correct_gains()` from `correct_calculations.py`
   - Accounts for all deposits (inflows) and withdrawals (outflows)
   - Interpolates positions onto unified timebase
   - Computes corrected position: $P_{\text{corr}}(t) = V(t) - D_{\text{cum}}(t) + W_{\text{cum}}(t)$
   - Computes gain: $G(t) = P_{\text{corr}}(t) - P_{\text{corr}}(t_0)$
   - Returns: $(t_1, G(t_1))$ where $G(t_1)$ is total gain in USD

5. **Residual Gating** (if enabled):
   - Fits baseline model (polynomial or median) to corrected position series
   - Computes residuals: $r(t) = P_{\text{corr}}(t) - \text{baseline}(t)$
   - Calculates $\sigma$ (standard deviation of residuals)
   - Checks if $|r(t_1)| > k \cdot \sigma$ (configurable $k$, default: 2.0)
   - If triggered: Sets decision = HOLD, $W_{\text{max}} = 0$
   - See [Residual Gating](#residual-gating) section for details

6. **Per-Wallet W_max Calculation** (if gate not triggered and $G(t_1) > 0$):
   - Queries wallet-level position breakdown from GreptimeDB
   - For each wallet $j$: $W_{j,\text{max}} = \text{safety\_factor} \cdot G_j(t_1)$
   - Where $G_j(t_1)$ is wallet-specific corrected gain
   - Safety factor $c \in (0, 1]$ (default: 0.5)
   - Clamps negative gains to 0

7. **Price Comparison** (Phase C, optional):
   - Fetches latest price from multiple sources (Liqwid GraphQL, Minswap Aggregator, GreptimeDB)
   - Computes absolute and relative deltas
   - Sets mismatch flag if delta exceeds threshold

8. **Diagnostic Chart Generation** (if enabled):
   - Calls `plot_residual_composite()` from `diagnostics.py`
   - Generates 3-panel chart: position view, residual plot, KDE distribution
   - Saves to `output/` directory with timestamp

9. **Decision Assembly**:
   - Populates `AssetDecision` dataclass:
     - `decision`: 1 (WITHDRAW_OK), 0 (HOLD), -1 (ERROR)
     - `wmax_usd`: List of `WalletBreakdown` objects
     - `g_usd`: Total corrected gain
     - `residual_usd`, `sigma_usd`, `k_sigma`, `residual_trigger`: Gating context
     - `prices_by_source`, `price_delta_abs`, `price_delta_rel`: Price comparison
     - `ref_mode`: "keyword", "data_range", or "null"
     - `t0_timestamp_seconds`, `t1_timestamp_seconds`: Reference and evaluation times

**Output**: Dictionary mapping `{asset_name: AssetDecision}`

---

### 3. HTTP Server & Metrics (`exporter.py`)

**Purpose**: Exposes Prometheus metrics, interactive dashboard, and JSON API endpoints.

**Class**: `MetricsExporter(settings)`

**HTTP Endpoints**:

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/metrics` | GET | Prometheus metrics (OpenMetrics format) | No |
| `/dashboard` | GET | Interactive HTML dashboard | No |
| `/api/assets` | GET | List of monitored assets (JSON) | No |
| `/api/decisions` | GET | Current decisions for all assets (JSON) | No |
| `/api/config/normalized` | GET | Normalized configuration (JSON) | Yes (if enabled) |
| `/api/sync/transactions` | POST | Trigger transaction sync from Liqwid API | Yes (if enabled) |

**Prometheus Metrics** (conditional, controlled by `telemetry.expose`):

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `wo_decision` | Gauge | asset, alternate_asset_name, ref_mode | Decision: 1=WITHDRAW_OK, 0=HOLD, -1=ERROR |
| `wo_wmax_usd` | Gauge | asset, alternate_asset_name, ref_mode | Max allowable withdrawal (USD) |
| `wo_v_ref_usd` | Gauge | asset, alternate_asset_name, ref_mode | Reference V_i(t0) raw position (USD) |
| `wo_v_t1_usd` | Gauge | asset, alternate_asset_name, ref_mode | Latest V_i(t1) raw position (USD) |
| `wo_g_usd` | Gauge | asset, alternate_asset_name, ref_mode | Corrected gain since t0 (USD) |
| `wo_price_t1_usd` | Gauge | asset, alternate_asset_name, ref_mode | Price at t1 (USD) |
| `wo_t0_timestamp_seconds` | Gauge | asset, alternate_asset_name, ref_mode | Reference timestamp (epoch) |
| `wo_t1_timestamp_seconds` | Gauge | asset, alternate_asset_name, ref_mode | Latest timestamp (epoch) |
| `wo_residual_usd` | Gauge | asset, alternate_asset_name, ref_mode | Residual at t1 (USD) |
| `wo_sigma_usd` | Gauge | asset, alternate_asset_name, ref_mode | Residual sigma (USD) |
| `wo_k_sigma` | Gauge | asset, alternate_asset_name, ref_mode | K-sigma threshold |
| `wo_residual_trigger` | Gauge | asset, alternate_asset_name, ref_mode | Residual trigger flag (0/1) |
| `wo_price_usd` | Gauge | asset, alternate_asset_name, source | Price by source (USD) |
| `wo_price_delta_abs` | Gauge | asset, alternate_asset_name | Abs price delta (USD) |
| `wo_price_delta_rel` | Gauge | asset, alternate_asset_name | Relative price delta |
| `wo_price_mismatch` | Gauge | asset, alternate_asset_name | Price mismatch flag (0/1) |
| `wo_rate_usd` | Gauge | asset, alternate_asset_name, source | USD rate time series |
| `wo_rate_ada` | Gauge | asset, alternate_asset_name, source | ADA rate time series |
| `wo_last_eval_timestamp_seconds` | Gauge | (none) | Last evaluation timestamp |

**Dashboard Features**:
- Asset selection dropdown
- Decision status indicator (colored badges)
- Per-wallet W_max breakdown table
- Diagnostic chart embed (position/residual/KDE)
- Price comparison table (if available)
- Residual gating status (threshold bands)
- Auto-refresh every 10 seconds

**Basic Authentication** (optional):
- Enabled via `orchestrator.auth.enabled: true`
- Username/password configured in YAML
- Required for `/api/config/normalized` and `/api/sync/transactions`
- Uses HTTP Basic Auth (RFC 7617)

---

### 4. Diagnostics (`diagnostics.py`)

**Purpose**: Generates diagnostic charts for visual analysis of position data and residuals.

**Main Function**: `plot_residual_composite(asset, reader, cfg, date_range, decision_dict, settings_orchestrator)`

**Chart Panels** (3-panel layout):

1. **Position View** (Top Panel):
   - X-axis: Time
   - Y-axis: Position (USD) or Rate (depending on view mode)
   - Lines:
     - Blue solid: Raw position $V(t)$ or rate
     - Orange dashed: Smoothed/baseline (polynomial fit or moving average)
     - Optional: $\sigma$ band (if `include_sigma_band: true`)
     - Optional: $k \cdot \sigma$ band (if `include_k_sigma_band: true`)
   - Scatter: Transaction markers (deposits: green ▲, withdrawals: red ▼)

2. **Residual Plot** (Middle Panel):
   - X-axis: Time
   - Y-axis: Residual (USD) - $r(t) = P_{\text{corr}}(t) - \text{baseline}(t)$
   - Horizontal lines:
     - Orange dashed: $\pm \sigma$ (1 standard deviation)
     - Red dashed: $\pm k \cdot \sigma$ (decision gate threshold)
   - Scatter: Residual values
   - Red marker: Latest residual $r(t_1)$

3. **KDE Distribution** (Bottom Panel):
   - X-axis: Residual value (USD)
   - Y-axis: Probability density
   - Curve: Gaussian KDE of residual distribution
   - Vertical lines:
     - Orange dashed: $\pm \sigma$
     - Red dashed: $\pm k \cdot \sigma$
     - Red solid: Latest residual $r(t_1)$

**View Modes**:
- `position` (default): Shows absolute position values
- `rate`: Shows rate of change (price_usd for USD rate, price_usd/ada_usd for ADA rate)

**Output**:
- Saved to `orchestrator.diagnostics.dir` (default: `output/`)
- Filename format: `{asset}_{timestamp}_residual_composite.png`
- Conditionally generated: Only if `diagnostics.enabled: true`

---

### 5. Configuration Management (`settings.py`)

**Purpose**: Loads, validates, and provides access to configuration settings.

**Main Class**: `Settings`

**Configuration Hierarchy** (YAML Schema):

> **Note**: The YAML configuration uses a modern hierarchical structure (`settings`, `domain`, `data`, etc.). 
> Internally, this is mapped to legacy Python dataclasses (`ClientConfig`, `OrchestratorConfig`) for backward compatibility.
> The structure below shows the **actual YAML schema** that users interact with.

```
orchestrator_config.yaml
├── settings:
│   ├── timezone: str (IANA timezone, e.g., "Asia/Tokyo")
│   └── currency: str (e.g., "usd")
│
├── domain:
│   └── assets: List[str] (e.g., ["djed", "usdc", "usdt", "usdm"])
│
├── data:
│   ├── databases:
│   │   └── greptime:
│   │       ├── host: str
│   │       ├── port: int
│   │       ├── database: str
│   │       └── timeout: int
│   ├── datasets:
│   │   └── transactions:
│   │       ├── alignment_method: str ("none" | "right_open" | "detect_spike" | "snap_to_*")
│   │       └── sources:
│   │           ├── liqwid:
│   │           │   ├── table_asset_prefix: str
│   │           │   ├── deposits_prefix: str
│   │           │   └── withdrawals_prefix: str
│   │           └── minswap:
│   │               └── table_price_prefix: str
│   └── date_range:
│       ├── start: str (ISO date, e.g., "2025-10-04")
│       └── end: str | null
│
├── prices:
│   ├── sources: List[str] (e.g., ["liqwid", "minswap"])
│   ├── duty_cycle_threshold: float
│   ├── endpoints:
│   │   ├── liqwid_graphql: str (URL)
│   │   ├── koios: str (URL)
│   │   └── minswap_aggregator: str (URL)
│   ├── priority_by_logical:
│   │   ├── liqwid: List[str] (e.g., ["greptime(liqwid)", "liqwid"])
│   │   └── minswap: List[str]
│   └── transaction_sync:
│       ├── start_date: str (ISO date)
│       └── end_date: str | null
│
├── analysis:
│   ├── trend_indicator:
│   │   ├── enabled: bool
│   │   ├── method: str ("moving_average" | "polynomial_fit")
│   │   ├── polynomial_order: int
│   │   ├── window_size_hours: float
│   │   ├── window_type: str ("polynomial" | "gaussian" | "boxcar" | "none")
│   │   ├── gaussian_kde_sigma_fraction: float
│   │   └── per_asset: Dict[str, AssetTrendParams]
│   ├── decision:
│   │   ├── reference:
│   │   │   ├── keyword: str (e.g., "alert_driven")
│   │   │   └── fallback: str | null (e.g., "data_range")
│   │   ├── gate:
│   │   │   ├── enabled: bool
│   │   │   ├── basis: str ("change_rate_usd" | "corrected_position")
│   │   │   ├── method: str ("median" | "polynomial_fit")
│   │   │   ├── polynomial_order: int
│   │   │   ├── threshold_mode: str ("stddev" | "percentile")
│   │   │   ├── central_confidence: float
│   │   │   ├── k_sigma: float
│   │   │   ├── min_points: int
│   │   │   ├── exclude_last_for_sigma: bool
│   │   │   ├── lookback_hours: float | null
│   │   │   └── sigma_epsilon: float
│   │   └── safety_factor:
│   │       └── c: float (safety multiplier, 0 < c ≤ 1)
│   └── price_compare:
│       ├── enabled: bool
│       ├── sources: str (references @prices.sources)
│       ├── epsilon_mode: str ("relative" | "absolute")
│       ├── tolerance_epsilon: float
│       ├── persistence_threshold: int
│       ├── action_on_mismatch: str ("hold" | "alert")
│       └── per_asset_overrides: Dict[str, AssetPriceCompareConfig]
│
├── runtime:
│   ├── auth:
│   │   └── enabled: bool
│   ├── schedule:
│   │   └── interval_minutes: int
│   └── telemetry:
│       ├── enabled: bool
│       ├── listen_address: str
│       ├── listen_port: int
│       ├── path: str
│       ├── metric_prefix: str
│       └── expose:
│           ├── decision: bool
│           ├── wmax_usd: bool
│           ├── residual_usd: bool
│           ├── sigma_usd: bool
│           ├── k_sigma: bool
│           ├── residual_trigger: bool
│           └── ... (see full list in config file)
│
├── visualization:
│   └── diagnostics:
│       ├── enabled: bool
│       ├── dir: str (output directory path)
│       ├── hist_samples_per_bin: int (histogram bin size)
│       ├── include_sigma_band: bool
│       ├── include_k_sigma_band: bool
│       └── lookback_hours_override: float | null
│
└── maintenance:
    └── cleanup:
        ├── enabled: bool
        ├── expire_before: str (e.g., "7d", "1w", "48h")
        └── paths: List[str] (directories to clean)
```

**Loading Process**:
1. Parse YAML file with modern schema (`settings`, `domain`, `data`, etc.)
2. Map new schema to legacy internal dataclasses via `_load_settings_new_schema()`:
   - `settings` + `domain` + `data` → `ClientConfig`
   - `analysis` + `runtime` + `visualization` + `maintenance` → `OrchestratorConfig`
3. Apply defaults for missing values
4. Validate required fields (e.g., `assets` must be non-empty)
5. Return `Settings` object containing `client: ClientConfig` and `orchestrator: OrchestratorConfig`

> **Internal Mapping Note**: The code internally uses `ClientConfig` and `OrchestratorConfig` dataclasses for 
> backward compatibility. The mapping layer in `settings.py` (_load_settings_new_schema, lines 613-893) 
> translates the modern YAML structure to these legacy classes transparently.

**Config Doctor** (`--print-config-normalized`):
- Calls `build_normalized_config()` from `config_normalizer.py`
- Resolves all defaults and per-asset overrides
- Outputs JSON for inspection/debugging

---

### 6. Transaction Syncer (`transaction_syncer.py`)

**Purpose**: Syncs transaction data from Liqwid API to GreptimeDB.

**Class**: `TransactionSyncer(liqwid_client, greptime_writer, settings)`

**Main Function**: `sync_transactions() -> SyncReport`

**Sync Process**:
1. **Wallet Discovery**:
   - Query Liqwid API for wallets with supply positions
   - Filter by configured assets

2. **Transaction Fetching**:
   - For each wallet, fetch deposit and withdrawal events
   - Parse timestamps, amounts, asset symbols

3. **Deduplication**:
   - Query existing transactions from GreptimeDB
   - Skip transactions already present (by timestamp + wallet + amount)

4. **Writing**:
   - Write new deposits to `liqwid_deposits_<asset>` table
   - Write new withdrawals to `liqwid_withdrawals_<asset>` table
   - Use `GreptimeWriter` for batch inserts

5. **Reporting**:
   - Return `SyncReport` with:
     - `deposits_inserted`: Count of new deposits
     - `withdrawals_inserted`: Count of new withdrawals
     - `errors`: List of error messages

**Triggering**:
- Manually via `POST /api/sync/transactions` endpoint
- Or programmatically via `TransactionSyncer.sync_transactions()` method

**Safety**: Uses `test_*` table prefix if `--test-prefix` flag enabled

---

### 7. Shared Modules

#### `greptime_reader.py`

**Purpose**: Query interface for GreptimeDB.

**Key Functions**:
- `fetch_asset_series(asset, date_range)`: Fetch position time series
- `fetch_transactions(asset, deposits_prefix, withdrawals_prefix, date_range)`: Fetch transaction data
- `fetch_price_series(asset, source, date_range)`: Fetch price time series
- `fetch_wallet_positions(asset, date_range)`: Fetch per-wallet position breakdown
- `test_connection()`: Verify GreptimeDB connectivity

#### `greptime_writer.py`

**Purpose**: Write interface for GreptimeDB.

**Key Functions**:
- `write_transactions(asset, transactions, is_deposit)`: Write transaction batch
- `write_price(asset, timestamp, price, source)`: Write single price point

#### `liqwid_client.py`

**Purpose**: HTTP client for Liqwid GraphQL API.

**Key Functions**:
- `query_wallets()`: Get wallets with supply positions
- `query_transactions(wallet, asset)`: Get deposits/withdrawals for wallet
- `query_prices(assets)`: Get latest prices (GraphQL)

#### `correct_calculations.py`

**Purpose**: Corrected gain calculation logic.

**Key Functions**:
- `calculate_correct_gains(position_timestamps, position_values, transactions, reference_time_index, interpolation_method, alignment_method)`: Main calculation
  - Returns: `(timebase, corrected_positions, deposits_cum, withdrawals_cum, gains)`
  
- `create_unified_timebase(position_timestamps, transactions)`: Merge timestamps from positions and transactions
- `interpolate_positions_on_timebase(position_timestamps, position_values, timebase, method)`: Interpolate positions onto unified timebase
- `create_transaction_vectors_on_timebase(transactions, timebase, alignment_method)`: Align transactions to timebase

**Calculation Formula**:
$$
P_{\text{corr}}(t) = V(t) - D_{\text{cum}}(t) + W_{\text{cum}}(t)
$$
$$
G(t) = P_{\text{corr}}(t) - P_{\text{corr}}(t_0)
$$

Where:
- $V(t)$: Raw position value at time $t$
- $D_{\text{cum}}(t)$: Cumulative deposits up to time $t$
- $W_{\text{cum}}(t)$: Cumulative withdrawals up to time $t$
- $P_{\text{corr}}(t)$: Corrected position at time $t$
- $G(t)$: Gain relative to reference time $t_0$

#### `resolver.py`

**Purpose**: Resolve display asset names (e.g., "USDT") to GreptimeDB symbols (e.g., "wanusdt").

**Class**: `Resolver(greptime_reader)`

**Key Function**: `resolve_asset(display_name) -> (market_id, symbol)`
- Queries Liqwid API for asset metadata (policy ID, token name)
- Maps display name to GreptimeDB table suffix
- Handles special cases (e.g., "DJED" → "djed", "USDM" → "usdm")

#### `models.py`

**Purpose**: Data structures for positions, transactions, and decisions.

**Key Classes**:
- `AssetTimeSeries`: Position time series (timestamps → values)
- `Transaction`: Deposit or withdrawal event
- `WalletBreakdown`: Per-wallet W_max calculation result

---

## Data Flow

### Evaluation Flow (One Iteration)

```
1. main.py::_evaluation_loop()
   └─ Sleeps for interval_minutes
   └─ Calls alert_logic.evaluate_once()
       │
       ▼
2. alert_logic.evaluate_once(reader, settings)
   │
   ├─ For each asset in settings.client.assets:
   │  │
   │  ├─ Resolve asset (display name → symbol)
   │  │  └─ resolver.resolve_asset(asset) → (market_id, symbol)
   │  │     └─ Queries Liqwid API for policy ID
   │  │
   │  ├─ Get reference point (t0, V(t0))
   │  │  └─ reference_state.get_last_reference(reader, cfg, [symbol], keyword)
   │  │     └─ Queries liqwid_withdrawals_<symbol> WHERE memo = keyword
   │  │     └─ Returns {symbol: (t0, V(t0))}
   │  │
   │  ├─ Fetch position data (t0 → t1)
   │  │  └─ reader.fetch_asset_series(symbol, DateRange(t0, t1))
   │  │     └─ Queries liqwid_supply_positions_<symbol>
   │  │     └─ Returns AssetTimeSeries {timestamp: value}
   │  │
   │  ├─ Fetch transaction data (t0 → t1)
   │  │  ├─ reader.fetch_transactions(symbol, deposits_prefix, withdrawals_prefix, DateRange(t0, t1))
   │  │  │  ├─ Queries liqwid_deposits_<symbol>
   │  │  │  └─ Queries liqwid_withdrawals_<symbol>
   │  │  └─ Returns List[Transaction]
   │  │
   │  ├─ Calculate corrected gains
   │  │  └─ correct_calculations.calculate_correct_gains(pos_ts, pos_vals, txs, ref_idx=0)
   │  │     ├─ create_unified_timebase(pos_ts, txs) → unified timestamps
   │  │     ├─ interpolate_positions_on_timebase(pos_ts, pos_vals, timebase, method="linear")
   │  │     ├─ create_transaction_vectors_on_timebase(txs, timebase, alignment_method)
   │  │     ├─ Compute corrected positions: P_corr = V - D_cum + W_cum
   │  │     └─ Compute gains: G = P_corr - P_corr[ref_idx]
   │  │     └─ Returns (timebase, P_corr, D_cum, W_cum, G)
   │  │
   │  ├─ Apply residual gating (if enabled)
   │  │  └─ decision_gate.apply_gate(P_corr, timebase, settings.orchestrator.decision_gate)
   │  │     ├─ Fit baseline (polynomial or median)
   │  │     ├─ Compute residuals: r = P_corr - baseline
   │  │     ├─ Calculate σ (std dev of residuals, optionally excluding last point)
   │  │     ├─ Check |r(t1)| > k * σ
   │  │     └─ Returns (triggered: bool, residual, sigma, k_sigma)
   │  │
   │  ├─ If gate triggered OR G(t1) <= 0:
   │  │  └─ Set decision = HOLD, wmax_usd = []
   │  │
   │  ├─ Else (gate not triggered AND G(t1) > 0):
   │  │  └─ Calculate per-wallet Wmax
   │  │     └─ _calculate_per_wallet_wmax(reader, cfg, symbol, date_range, safety_factor.c)
   │  │        ├─ reader.fetch_wallet_positions(symbol, date_range)
   │  │        │  └─ Queries liqwid_supply_positions_<symbol> GROUP BY wallet_address
   │  │        ├─ For each wallet:
   │  │        │  ├─ Fetch wallet position series
   │  │        │  ├─ Fetch wallet transactions
   │  │        │  ├─ Calculate wallet-specific corrected gain: G_j(t1)
   │  │        │  ├─ Compute: W_j_max = c * max(0, G_j(t1))
   │  │        │  └─ Create WalletBreakdown(wallet, W_j_max)
   │  │        └─ Returns List[WalletBreakdown]
   │  │
   │  ├─ Fetch price comparison (if enabled)
   │  │  ├─ price_source.LiqwidGraphQLPriceSource.get_price(asset)
   │  │  ├─ price_source.MinswapAggregatorPriceSource.get_price(asset)
   │  │  ├─ Compute deltas: abs, relative
   │  │  └─ Set mismatch flag
   │  │
   │  ├─ Generate diagnostic chart (if enabled)
   │  │  └─ diagnostics.plot_residual_composite(asset, reader, cfg, date_range, decision_dict, settings_orc)
   │  │     ├─ Fetch data (positions, transactions, smoothed baseline)
   │  │     ├─ Compute residuals
   │  │     ├─ Fit Gaussian KDE
   │  │     ├─ Create 3-panel matplotlib figure
   │  │     └─ Save to output/{asset}_{timestamp}_residual_composite.png
   │  │
   │  └─ Assemble AssetDecision
   │     └─ AssetDecision(
   │          decision=1 (WITHDRAW_OK) or 0 (HOLD) or -1 (ERROR),
   │          wmax_usd=List[WalletBreakdown],
   │          g_usd=G(t1),
   │          residual_usd=r(t1),
   │          sigma_usd=σ,
   │          k_sigma=k,
   │          residual_trigger=0/1,
   │          prices_by_source={...},
   │          ref_mode="keyword" | "data_range" | "null",
   │          t0_timestamp_seconds=t0.timestamp(),
   │          t1_timestamp_seconds=t1.timestamp(),
   │          ...
   │        )
   │
   └─ Returns Dict[str, AssetDecision]
       │
       ▼
3. exporter.py::MetricsExporter.update_metrics(decisions)
   │
   ├─ For each asset, decision in decisions.items():
   │  │
   │  ├─ Update Prometheus gauges (if exposed):
   │  │  ├─ wo_decision.labels(asset, alternate_name, ref_mode).set(decision.decision)
   │  │  ├─ wo_wmax_usd.labels(...).set(sum(wb.wmax_usd for wb in decision.wmax_usd))
   │  │  ├─ wo_g_usd.labels(...).set(decision.g_usd)
   │  │  ├─ wo_residual_usd.labels(...).set(decision.residual_usd)
   │  │  ├─ wo_sigma_usd.labels(...).set(decision.sigma_usd)
   │  │  ├─ wo_residual_trigger.labels(...).set(decision.residual_trigger)
   │  │  └─ ... (other metrics)
   │  │
   │  └─ Store latest_decisions[asset] = decision (for dashboard API)
   │
   └─ Set wo_last_eval_timestamp_seconds.set(time.time())
```

---

## Decision Logic

### Decision States

| Decision Value | Name | Meaning |
|----------------|------|---------|
| 1 | WITHDRAW_OK | Withdrawals allowed, $W_{\text{max}} > 0$ |
| 0 | HOLD | Withdrawals not recommended, $W_{\text{max}} = 0$ |
| -1 | ERROR | Evaluation failed (e.g., no data, resolution error) |

### Decision Flow

```
┌─────────────────────────────────────────┐
│     Resolve Asset (display → symbol)     │
└───────────┬─────────────────────────────┘
            │
            ▼
    ┌───────────────┐
    │ Resolution OK? │────── NO ─────▶ decision = -1 (ERROR)
    └───────┬───────┘
            │ YES
            ▼
┌─────────────────────────────────────────┐
│   Find Reference Point (t0, V(t0))      │
│   (tagged withdrawal with keyword)      │
└───────────┬─────────────────────────────┘
            │
            ▼
    ┌──────────────────┐
    │ Reference found? │────── NO ─────▶ Apply Fallback:
    └────────┬─────────┘                  • "data_range" → Use date_range.start as t0
            │ YES                        • "null" → decision = 0 (HOLD)
            ▼
┌─────────────────────────────────────────┐
│  Fetch Position & Transaction Data      │
│        (t0 → t1, t1 = now UTC)          │
└───────────┬─────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────┐
│     Calculate Corrected Gains G(t1)     │
│   (account for deposits/withdrawals)    │
└───────────┬─────────────────────────────┘
            │
            ▼
    ┌───────────────────┐
    │ Decision Gate     │
    │ Enabled?          │
    └────────┬──────────┘
             │
        ┌────┴─────┐
        │          │
       YES        NO
        │          │
        │          ▼
        │     (Use baseline decision logic)
        │          │
        │          ▼
        │     ┌───────────────┐
        │     │  G(t1) > 0 ?  │
        │     └───────┬───────┘
        │             │
        │        ┌────┴─────┐
        │       YES        NO
        │        │          │
        │        │          ▼
        │        │     decision = 0
        │        │     wmax = []
        │        │     (HOLD, reason: non-positive gains)
        │        │
        │        ▼
        │   ┌─────────────────────────────┐
        │   │ Calculate Per-Wallet W_max: │
        │   │                             │
        │   │ For each wallet j:          │
        │   │   G_j = wallet-specific     │
        │   │         corrected gain      │
        │   │   W_j_max = c * max(0, G_j) │
        │   │                             │
        │   │ (c = safety factor)         │
        │   └────────────┬────────────────┘
        │                │
        │                ▼
        │         decision = 1
        │         wmax = [WalletBreakdown(...), ...]
        │         (WITHDRAW_OK)
        │
        ▼
 (Residual-based decision logic)
        │
        ▼
 ┌──────────────────────────────┐
 │ Compute Residuals:            │
 │ r(t) = P_corr(t) - baseline(t)│
 │                               │
 │ Calculate σ (std dev)         │
 │                               │
 │ Check: |r(t1)| > k * σ ?      │
 └──────────┬────────────────────┘
            │
       ┌────┴─────┐
       │          │
   TRIGGERED   NOT TRIGGERED
       │          │
       ▼          ▼
  decision = 1   decision = 0
  (trigger value based on residual threshold)
  
  Note: When gate enabled, decision is purely based on
  residual trigger (0 or 1), NOT on gains.
  Wmax is still calculated but may be ignored if trigger=0.
```

---

## Price Sources

The orchestrator supports multiple price sources with priority-based fallback:

### 1. Liqwid GraphQL API
- **URL**: Configured via `orchestrator.apis.liqwid_graphql`
- **Usage**: Real-time price queries via GraphQL
- **Pros**: Official Liqwid prices, low latency
- **Cons**: Requires network access

### 2. Minswap Aggregator API
- **URL**: Configured via `orchestrator.apis.minswap_aggregator`
- **Usage**: Real-time price queries via HTTP
- **Requires**: Token registry (`config/token_registry.csv`) with policy IDs and token names
- **Pros**: Independent price source for comparison
- **Cons**: Requires token registry setup

### 3. GreptimeDB (Cached Prices)
- **Source**: `liqwid_supply_positions_<asset>` table (price_usd column)
- **Usage**: Latest price from position data
- **Pros**: Always available, no external dependency
- **Cons**: May be stale (depends on position update frequency)

### Priority Logic

Configured via `orchestrator.telemetry.price_source_priority`:

```yaml
orchestrator:
  telemetry:
    price_source_priority:
      liqwid:
        - "greptime(liqwid)"    # Try GreptimeDB first
        - "liqwid_graphql"      # Fallback to GraphQL API
      minswap:
        - "greptime(minswap)"   # Try GreptimeDB first
        - "minswap_aggregator"  # Fallback to Aggregator API
```

If a source fails (timeout, error), the orchestrator tries the next source in the priority list.

---

## Residual Gating

**Purpose**: Prevent withdrawals during anomalous market conditions (e.g., sudden price spikes, data quality issues).

### Concept

1. **Baseline Model**: Fit a smooth curve (polynomial or median) to corrected position series $P_{\text{corr}}(t)$
2. **Residuals**: Compute deviations from baseline: $r(t) = P_{\text{corr}}(t) - \text{baseline}(t)$
3. **Threshold**: Calculate $\sigma$ (standard deviation of residuals)
4. **Gating Rule**: If $|r(t_1)| > k \cdot \sigma$, set decision = HOLD

### Configuration

```yaml
orchestrator:
  decision_gate:
    enabled: true                     # Enable residual gating (applies to all configured assets)
    basis: "corrected_position"       # Or "change_rate_usd"
    method: "polynomial_fit"          # Or "median"
    polynomial_order: 2               # Degree of polynomial (if method=polynomial_fit)
    k_sigma: 2.0                      # Threshold multiplier (2σ rule)
    min_points: 10                    # Minimum data points required
    exclude_last_for_sigma: false     # Exclude last point from σ calculation
    lookback_hours: 48.0              # Only use last 48 hours of data
    sigma_epsilon: 1e-6               # Small value to avoid division by zero
    apply_in_fallback: true           # Apply gate even if using fallback reference mode
    threshold_mode: "stddev"          # Or "percentile"
    central_confidence: 0.95          # Central interval mass (if threshold_mode=percentile)
```

**Note**: The `whitelist` field has been removed. Decision gate now applies to all configured assets when enabled.
Per-asset overrides via `per_asset` are still supported in configuration but not actively used in current implementation.

### Threshold Modes

#### 1. Standard Deviation (`threshold_mode: "stddev"`)

- **Formula**: $|r(t_1)| > k \cdot \sigma$
- **Interpretation**: Residual exceeds $k$ standard deviations from mean
- **Typical k values**: 2.0 (95% confidence), 3.0 (99.7% confidence)

#### 2. Percentile (`threshold_mode: "percentile"`)

- **Formula**: $r(t_1)$ outside central $p\%$ interval of residual distribution
- **Interpretation**: Residual in outer $(1 - p)$ tail of distribution
- **Typical p values**: 0.95 (5% tail), 0.99 (1% tail)
- **Calculation**: Uses quantiles of empirical residual distribution

### Baseline Methods

#### 1. Polynomial Fit (`method: "polynomial_fit"`)

- Fits polynomial of degree `polynomial_order` (default: 2) using least squares
- Good for capturing smooth trends
- May overfit if order too high

#### 2. Median (`method: "median"`)

- Uses flat median line as baseline
- Robust to outliers
- Simple, no parameters

### Visualization

Residual gating status is shown in:
- **Dashboard**: Threshold bands ($\pm \sigma$, $\pm k \cdot \sigma$) and latest residual indicator
- **Diagnostic Charts**: Residual plot panel with threshold lines
- **Metrics**: `wo_residual_trigger{asset}` (0 = not triggered, 1 = triggered)

---

## Configuration System

### Configuration File Structure

> **Note**: This shows the **actual YAML structure**. Internally, settings are mapped to legacy Python dataclasses
> (`ClientConfig`, `OrchestratorConfig`) for backward compatibility with existing code.

```yaml
# config/orchestrator_config.yaml

settings:
  timezone: "Asia/Tokyo"
  currency: "usd"

domain:
  assets: ["djed", "usdc", "usdt", "usdm"]

data:
  databases:
    greptime:
      host: "http://192.168.1.12"
      port: 7010
      database: "liqwid"
      timeout: 10
  datasets:
    transactions:
      alignment_method: "detect_spike"  # none | right_open | detect_spike | snap_to_*
      sources:
        liqwid:
          table_asset_prefix: "liqwid_supply_positions_"
          deposits_prefix: "liqwid_deposits_"
          withdrawals_prefix: "liqwid_withdrawals_"
        minswap:
          table_price_prefix: "minswap_prices_"
  date_range:
    start: "2025-10-04"
    end: null

prices:
  sources: ["liqwid", "minswap"]
  duty_cycle_threshold: 0.9
  endpoints:
    liqwid_graphql: "https://v2.api.liqwid.finance/graphql"
    koios: "https://api.koios.rest/api/v1"
    minswap_aggregator: "https://agg-api.minswap.org"
  priority_by_logical:
    liqwid: ["greptime(liqwid)", "liqwid"]
    minswap: ["greptime(minswap)", "minswap"]
  transaction_sync:
    start_date: "2025-07-01"
    end_date: null

analysis:
  trend_indicator:
    enabled: true
    method: "polynomial_fit"     # moving_average | polynomial_fit
    polynomial_order: 2
    window_size_hours: 24.0
    window_type: "polynomial"    # polynomial | gaussian | boxcar | none
    gaussian_kde_sigma_fraction: 0.3
    per_asset: {}
  decision:
    reference:
      keyword: "alert_driven"
      fallback: "data_range"
    gate:
      enabled: true
      basis: "change_rate_usd"   # change_rate_usd | corrected_position
      method: "median"           # median | polynomial_fit
      polynomial_order: 1
      threshold_mode: "percentile"  # stddev | percentile
      central_confidence: 0.68
      k_sigma: 2.0
      min_points: 20
      exclude_last_for_sigma: true
      lookback_hours: null
      sigma_epsilon: 1e-6
    safety_factor:
      c: 0.5   # Safety multiplier (0 < c ≤ 1)
  price_compare:
    enabled: true
    sources: "@prices.sources"  # References prices.sources list
    epsilon_mode: "relative"
    tolerance_epsilon: 0.01
    persistence_threshold: 1
    action_on_mismatch: "hold"
    per_asset_overrides: {}

runtime:
  auth:
    enabled: true
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
      residual_usd: true
      sigma_usd: true
      k_sigma: true
      residual_trigger: true
      # ... (see full list in orchestrator_config.yaml)

visualization:
  diagnostics:
    enabled: false
    dir: "output/plots"
    hist_samples_per_bin: 10       # Histogram bin size
    include_sigma_band: false
    include_k_sigma_band: false
    lookback_hours_override: null

maintenance:
  cleanup:
    enabled: true
    expire_before: "7d"   # Delete files older than 7 days
    paths:
      - "client/output"
      - "output"
```

### Token Registry Format

```csv
# config/token_registry.csv
asset,policy_id,token_name_hex
djed,8db269c3ec630e06ae29f74bc39edd1f87c819f1056206e879a1cd61,446a656432
usdm,c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad,0014df105553444d
usdc,placeholder_policy_id,placeholder_token_hex
usdt,placeholder_policy_id,placeholder_token_hex
```

**Purpose**: Maps display asset names to Cardano token identifiers for Minswap API queries.

---

## Database Schema

### GreptimeDB Tables

#### 1. Position Tables: `liqwid_supply_positions_<asset>`

| Column | Type | Description |
|--------|------|-------------|
| `ts` | Timestamp | Event timestamp (primary key) |
| `wallet_address` | String | Wallet address |
| `market_id` | String | Liqwid market identifier |
| `q_token_balance` | Float | qToken balance |
| `underlying_units` | Float | Underlying asset units |
| `value_usd` | Float | Position value in USD |
| `price_usd` | Float | Asset price in USD |
| `price_ada` | Float | Asset price in ADA |

**Queries**:
```sql
-- Fetch position series for asset
SELECT ts, value_usd FROM liqwid_supply_positions_djed
WHERE ts >= '2025-01-01' AND ts <= '2025-01-21'
ORDER BY ts;

-- Fetch per-wallet breakdown
SELECT wallet_address, MAX(value_usd) as latest_value
FROM liqwid_supply_positions_djed
WHERE ts >= '2025-01-01' AND ts <= '2025-01-21'
GROUP BY wallet_address;
```

#### 2. Deposit Tables: `liqwid_deposits_<asset>`

| Column | Type | Description |
|--------|------|-------------|
| `ts` | Timestamp | Transaction timestamp (primary key) |
| `wallet_address` | String | Depositor wallet address |
| `market_id` | String | Liqwid market identifier |
| `amount` | Float | Deposit amount (underlying units) |
| `tx_hash` | String | Transaction hash |

**Queries**:
```sql
-- Fetch deposits for date range
SELECT ts, wallet_address, amount FROM liqwid_deposits_djed
WHERE ts >= '2025-01-01' AND ts <= '2025-01-21'
ORDER BY ts;
```

#### 3. Withdrawal Tables: `liqwid_withdrawals_<asset>`

| Column | Type | Description |
|--------|------|-------------|
| `ts` | Timestamp | Transaction timestamp (primary key) |
| `wallet_address` | String | Withdrawer wallet address |
| `market_id` | String | Liqwid market identifier |
| `amount` | Float | Withdrawal amount (underlying units) |
| `tx_hash` | String | Transaction hash |
| `memo` | String | Optional memo/tag (e.g., "alert_driven_withdrawal") |

**Queries**:
```sql
-- Fetch tagged withdrawals (reference points)
SELECT ts, wallet_address, amount FROM liqwid_withdrawals_djed
WHERE memo = 'alert_driven_withdrawal'
ORDER BY ts DESC
LIMIT 1;

-- Fetch all withdrawals for date range
SELECT ts, wallet_address, amount, memo FROM liqwid_withdrawals_djed
WHERE ts >= '2025-01-01' AND ts <= '2025-01-21'
ORDER BY ts;
```

---

## External Dependencies

### Required Services

1. **GreptimeDB**:
   - Version: 0.5+
   - Connection: HTTP (default port 4000)
   - Purpose: Time-series storage for positions, transactions, prices

2. **Python Dependencies**:
   - Core: `requests`, `pyyaml`, `numpy`, `matplotlib`
   - Metrics: `prometheus_client`
   - See `requirements.txt` for full list

### Optional Services

1. **Liqwid GraphQL API**:
   - Purpose: Real-time price queries, transaction fetching
   - Required for: Price comparison, transaction sync
   - Fallback: Can use GreptimeDB-cached prices if unavailable

2. **Minswap Aggregator API**:
   - Purpose: Independent price source for comparison
   - Required for: Price mismatch detection
   - Requires: Token registry setup

---

## Design Decisions

### 1. Why Stateless?

**Decision**: No persistent state between evaluations (all data fetched fresh each iteration).

**Rationale**:
- **Simplicity**: No state management complexity, no database for state
- **Reliability**: Restarts don't lose state
- **Debugging**: Each evaluation is independent and reproducible
- **Consistency**: Always uses latest data from authoritative source (GreptimeDB)

**Trade-off**: Higher query load on GreptimeDB (mitigated by efficient queries and caching in DB)

### 2. Why Per-Wallet W_max?

**Decision**: Calculate separate W_max for each wallet instead of global W_max.

**Rationale**:
- **Fairness**: Each wallet's withdrawals based on their individual gains
- **Granularity**: Allows selective withdrawals from profitable wallets
- **Transparency**: Dashboard shows breakdown per wallet

**Trade-off**: More computation (one gain calculation per wallet), but negligible overhead

### 3. Why Residual Gating?

**Decision**: Use statistical residual analysis to gate withdrawals during anomalies.

**Rationale**:
- **Safety**: Prevents withdrawals during data quality issues or market manipulation
- **Flexibility**: Configurable thresholds and methods per asset
- **Transparency**: Residual values exposed in metrics and charts

**Trade-off**: May be overly conservative if threshold too low (adjust `k_sigma`)

### 4. Why Prometheus Metrics?

**Decision**: Use Prometheus for metrics instead of custom monitoring.

**Rationale**:
- **Standard**: Industry-standard monitoring solution
- **Integration**: Works with Grafana, Alertmanager, etc.
- **Scalability**: Efficient time-series storage and queries
- **Ecosystem**: Rich ecosystem of exporters and tools

**Alternative**: Could use InfluxDB, Datadog, or custom solution

### 5. Why YAML Configuration?

**Decision**: Use YAML for configuration instead of JSON or TOML.

**Rationale**:
- **Readability**: Comments, multi-line strings, no quotes required
- **Reuse**: Consistent with client configuration
- **Tooling**: Good editor support (syntax highlighting, validation)

**Trade-off**: YAML parsing quirks (e.g., `true`/`yes` ambiguity), but using safe loader

### 6. Why GreptimeDB?

**Decision**: Use GreptimeDB as primary data store.

**Rationale**:
- **Time-Series Optimized**: Efficient storage and queries for position/transaction data
- **SQL Interface**: Familiar query language (vs. InfluxQL, PromQL)
- **HTTP API**: No special client library required
- **Multi-Tenancy**: Separate databases for test/prod

**Alternative**: Could use InfluxDB, TimescaleDB, or PostgreSQL

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)
