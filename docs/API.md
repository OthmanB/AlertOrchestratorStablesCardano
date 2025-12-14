# Alert Orchestrator API Reference

This document describes all HTTP endpoints exposed by the Alert Orchestrator.

## Table of Contents

1. [Base URL](#base-url)
2. [Authentication](#authentication)
3. [Endpoints](#endpoints)
4. [Response Formats](#response-formats)
5. [Error Handling](#error-handling)
6. [Code Examples](#code-examples)

---

## Base URL

The orchestrator HTTP server listens on:

```
http://<host>:<port>
```

**Default**: `http://localhost:9808`

**Configuration**:
```yaml
orchestrator:
  telemetry:
    listen_address: "0.0.0.0"  # Bind address
    listen_port: 9808          # Port
```

---

## Authentication

### Basic Authentication

Some endpoints require HTTP Basic Authentication when enabled in configuration:

```yaml
orchestrator:
  auth:
    enabled: true
    username: "admin"
    password_hash: "sha256:<hash>"  # Use bcrypt in production
```

**Protected Endpoints**:
- `GET /api/config/normalized`
- `POST /api/sync/transactions`

**Header Format**:
```
Authorization: Basic <base64(username:password)>
```

**Example**:
```bash
curl -u admin:password http://localhost:9808/api/config/normalized
```

---

## Endpoints

### 1. GET /metrics

**Purpose**: Prometheus metrics (OpenMetrics format)

**Authentication**: None

**Query Parameters**: None

**Response Format**: Text (Prometheus/OpenMetrics)

**Response Example**:
```prometheus
# HELP wo_decision Decision: 1=WITHDRAW_OK, 0=HOLD, -1=ERROR
# TYPE wo_decision gauge
wo_decision{asset="djed",alternate_asset_name="DJED",ref_mode="keyword"} 1.0
wo_decision{asset="usdm",alternate_asset_name="USDM",ref_mode="keyword"} 0.0

# HELP wo_wmax_usd Max allowable withdrawal in USD
# TYPE wo_wmax_usd gauge
wo_wmax_usd{asset="djed",alternate_asset_name="DJED",ref_mode="keyword"} 1234.56
wo_wmax_usd{asset="usdm",alternate_asset_name="USDM",ref_mode="keyword"} 0.0

# HELP wo_g_usd Corrected gain since t0 (USD)
# TYPE wo_g_usd gauge
wo_g_usd{asset="djed",alternate_asset_name="DJED",ref_mode="keyword"} 2469.12

# HELP wo_residual_usd Residual at t1 (USD)
# TYPE wo_residual_usd gauge
wo_residual_usd{asset="djed",alternate_asset_name="DJED",ref_mode="keyword"} 45.67

# HELP wo_sigma_usd Residual sigma (USD)
# TYPE wo_sigma_usd gauge
wo_sigma_usd{asset="djed",alternate_asset_name="DJED",ref_mode="keyword"} 32.10

# HELP wo_residual_trigger Residual trigger flag (0/1)
# TYPE wo_residual_trigger gauge
wo_residual_trigger{asset="djed",alternate_asset_name="DJED",ref_mode="keyword"} 0.0

# HELP wo_last_eval_timestamp_seconds Last evaluation timestamp (epoch seconds)
# TYPE wo_last_eval_timestamp_seconds gauge
wo_last_eval_timestamp_seconds 1737469200.0
```

**Metric Labels**:
- `asset`: Display asset name (e.g., "djed")
- `alternate_asset_name`: Human-readable name (e.g., "DJED")
- `ref_mode`: Reference mode ("keyword", "data_range", "null")
- `source`: Price source (for price metrics only)

**Usage**:
```bash
# Fetch all metrics
curl http://localhost:9808/metrics

# Scrape with Prometheus
# Add to prometheus.yml:
scrape_configs:
  - job_name: 'alert_orchestrator'
    static_configs:
      - targets: ['localhost:9808']
```

**Status Codes**:
- `200 OK`: Metrics successfully generated

---

### 2. GET /dashboard

**Purpose**: Interactive HTML dashboard for monitoring

**Authentication**: None

**Query Parameters**:
- `asset` (optional): Select specific asset (e.g., `?asset=djed`)
- `view` (optional): Chart view mode (`position` or `rate`, default: `position`)
- `autorefresh` (optional): Enable auto-refresh (`true` or `false`, default: `true`)

**Response Format**: HTML

**Response Example**: (HTML page with JavaScript)

**Features**:
- Asset selection dropdown
- Decision status badge (color-coded)
- Per-wallet W_max breakdown table
- Diagnostic chart embed (3-panel: position, residual, KDE)
- Residual gating status display
- Price comparison table (if available)
- Auto-refresh every 10 seconds

**Usage**:
```bash
# View dashboard
open http://localhost:9808/dashboard

# View specific asset
open http://localhost:9808/dashboard?asset=djed

# View in rate mode
open http://localhost:9808/dashboard?asset=djed&view=rate

# Disable auto-refresh
open http://localhost:9808/dashboard?autorefresh=false
```

**Status Codes**:
- `200 OK`: Dashboard HTML returned

---

### 3. GET /api/assets

**Purpose**: Get list of monitored assets with selection state

**Authentication**: None

**Query Parameters**:
- `asset` (optional): Pre-select specific asset

**Response Format**: JSON

**Response Schema**:
```json
{
  "assets": ["string"],        // Array of asset names (alphabetically sorted)
  "selected": "string" | null  // Currently selected asset (from query param)
}
```

**Response Example**:
```json
{
  "assets": ["djed", "usdm", "wanusdc", "wanusdt"],
  "selected": "djed"
}
```

**Usage**:
```bash
# Get all assets
curl http://localhost:9808/api/assets

# Get assets with selection
curl http://localhost:9808/api/assets?asset=djed
```

**Status Codes**:
- `200 OK`: Assets list returned

---

### 4. GET /api/decisions

**Purpose**: Get current decisions for all assets (latest evaluation results)

**Authentication**: None

**Query Parameters**: None

**Response Format**: JSON

**Response Schema**:
```json
{
  "<asset>": {
    "decision": number,                    // 1=WITHDRAW_OK, 0=HOLD, -1=ERROR
    "ref_mode": string,                    // "keyword" | "data_range" | "null"
    "wmax_usd": number,                    // Total W_max (sum of all wallets)
    "wallet_breakdown": [                  // Per-wallet breakdown
      {
        "wallet_address": string,          // Full wallet address
        "abbreviated_address": string,     // Abbreviated (first 8 + last 4)
        "wmax_usd": number                 // W_max for this wallet
      }
    ],
    "residual_usd": number | null,         // Residual at t1
    "sigma_usd": number | null,            // Residual sigma
    "k_sigma": number | null,              // k-sigma threshold
    "residual_trigger": number | null,     // 0=not triggered, 1=triggered
    "debug_plot_path": string | null,      // Path to diagnostic chart
    "t0_ts": number | null,                // Reference timestamp (epoch seconds)
    "t1_ts": number | null,                // Evaluation timestamp (epoch seconds)
    "prices_by_source": {                  // Price comparison (if available)
      "<source>": number
    },
    "price_delta_abs": number | null,      // Absolute price delta
    "price_delta_rel": number | null,      // Relative price delta
    "price_mismatch": number | null,       // 0=OK, 1=mismatch
    "price_compare_unavailable": number | null  // 0=available, 1=unavailable
  }
}
```

**Response Example**:
```json
{
  "djed": {
    "decision": 1,
    "ref_mode": "keyword",
    "wmax_usd": 1234.56,
    "wallet_breakdown": [
      {
        "wallet_address": "addr1qxe7h8vw2m5c8n9p0l1k3j5h6g4f2d1s0a9z8y7x6w5v4u3t2r1q0p",
        "abbreviated_address": "addr1qxe...1q0p",
        "wmax_usd": 456.78
      },
      {
        "wallet_address": "addr1qyf8i9wx3n6d9q2m4l6k8j0h2g5f3e1s1b0c9z8y7x6w5v4u3t2r",
        "abbreviated_address": "addr1qyf...3t2r",
        "wmax_usd": 389.12
      },
      {
        "wallet_address": "addr1qzg9j0xy4o7e0r3n5m7l9k1i3h6g4f2e2s2c1d0z9y8x7w6v5u4t",
        "abbreviated_address": "addr1qzg...5u4t",
        "wmax_usd": 388.66
      }
    ],
    "residual_usd": 45.67,
    "sigma_usd": 32.10,
    "k_sigma": 2.0,
    "residual_trigger": 0,
    "debug_plot_path": "output/djed_20250121_143022_residual_composite.png",
    "t0_ts": 1737388800.0,
    "t1_ts": 1737469200.0,
    "prices_by_source": {
      "liqwid_graphql": 1.0023,
      "greptime(liqwid)": 1.0020
    },
    "price_delta_abs": 0.0003,
    "price_delta_rel": 0.0003,
    "price_mismatch": 0,
    "price_compare_unavailable": 0
  },
  "usdm": {
    "decision": 0,
    "ref_mode": "keyword",
    "wmax_usd": 0.0,
    "wallet_breakdown": [],
    "residual_usd": 123.45,
    "sigma_usd": 50.00,
    "k_sigma": 2.0,
    "residual_trigger": 1,
    "debug_plot_path": "output/usdm_20250121_143022_residual_composite.png",
    "t0_ts": 1737388800.0,
    "t1_ts": 1737469200.0,
    "prices_by_source": {},
    "price_delta_abs": null,
    "price_delta_rel": null,
    "price_mismatch": null,
    "price_compare_unavailable": null
  }
}
```

**Usage**:
```bash
# Get all decisions
curl http://localhost:9808/api/decisions | jq

# Get specific asset decision
curl http://localhost:9808/api/decisions | jq '.djed'

# Check if residual gate triggered
curl http://localhost:9808/api/decisions | jq '.djed.residual_trigger'
```

**Status Codes**:
- `200 OK`: Decisions data returned

---

### 5. GET /api/config/normalized

**Purpose**: Get normalized configuration (config doctor) for debugging

**Authentication**: **Required** (if `orchestrator.auth.enabled: true`)

**Query Parameters**: None

**Response Format**: JSON

**Response Schema**:
```json
{
  "client": {
    "greptime": { ... },
    "assets": ["string"],
    ...
  },
  "orchestrator": {
    "reference_keyword": "string",
    "safety_factor": { "c": number },
    "decision_gate": { ... },
    ...
  }
}
```

**Response Example**: (Full normalized configuration with all defaults applied)

**Usage**:
```bash
# Without authentication (if auth disabled)
curl http://localhost:9808/api/config/normalized | jq

# With authentication (if auth enabled)
curl -u admin:password http://localhost:9808/api/config/normalized | jq

# Save to file
curl -u admin:password http://localhost:9808/api/config/normalized > normalized_config.json
```

**Status Codes**:
- `200 OK`: Configuration returned
- `401 Unauthorized`: Authentication required or failed
- `500 Internal Server Error`: Configuration build failed

---

### 6. POST /api/sync/transactions

**Purpose**: Trigger transaction sync from Liqwid API to GreptimeDB

**Authentication**: **Required** (if `orchestrator.auth.enabled: true`)

**Query Parameters**: None

**Request Body**: None

**Response Format**: JSON

**Response Schema**:
```json
{
  "status": "string",              // "success" | "error"
  "deposits_inserted": number,     // Number of new deposits written
  "withdrawals_inserted": number,  // Number of new withdrawals written
  "errors": ["string"],            // Error messages (if any)
  "message": "string"              // Human-readable message
}
```

**Response Example (Success)**:
```json
{
  "status": "success",
  "deposits_inserted": 42,
  "withdrawals_inserted": 15,
  "errors": [],
  "message": "Transaction sync completed successfully"
}
```

**Response Example (Partial Failure)**:
```json
{
  "status": "error",
  "deposits_inserted": 30,
  "withdrawals_inserted": 10,
  "errors": [
    "Failed to sync asset 'wanusdc': API timeout",
    "Failed to write transactions for 'wanusdt': Database error"
  ],
  "message": "Transaction sync completed with errors"
}
```

**Usage**:
```bash
# Without authentication (if auth disabled)
curl -X POST http://localhost:9808/api/sync/transactions

# With authentication (if auth enabled)
curl -X POST -u admin:password http://localhost:9808/api/sync/transactions

# With JSON output
curl -X POST -u admin:password http://localhost:9808/api/sync/transactions | jq
```

**Status Codes**:
- `200 OK`: Sync completed (check `status` field for success/error)
- `401 Unauthorized`: Authentication required or failed
- `500 Internal Server Error`: Sync failed to start

**Notes**:
- Sync is idempotent (duplicate transactions are skipped)
- Respects `--test-prefix` flag (writes to `test_*` tables if enabled)
- May take several minutes for large wallets/assets

---

## Response Formats

### JSON

All API endpoints (except `/metrics` and `/dashboard`) return JSON.

**Content-Type**: `application/json`

**Encoding**: UTF-8

**Pretty-print**: Not enabled by default (use `jq` for formatting)

### Prometheus/OpenMetrics

`/metrics` endpoint returns Prometheus-compatible metrics.

**Content-Type**: `text/plain; version=0.0.4; charset=utf-8`

**Format**: OpenMetrics text format
- Comments: Lines starting with `#`
- Metric lines: `<metric_name>{<labels>} <value>`

### HTML

`/dashboard` endpoint returns HTML page.

**Content-Type**: `text/html; charset=utf-8`

**Features**:
- Responsive layout (works on mobile)
- Embedded JavaScript for interactivity
- CSS for styling

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | When Used |
|------|---------|-----------|
| 200 | OK | Request succeeded |
| 401 | Unauthorized | Authentication required or failed |
| 404 | Not Found | Endpoint does not exist |
| 500 | Internal Server Error | Server-side error (check logs) |

### Error Response Format

For errors, some endpoints return JSON:

```json
{
  "error": "string",    // Error message
  "status": "error"     // Status indicator
}
```

**Example**:
```json
{
  "error": "failed to build normalized config: invalid safety_factor.c",
  "status": "error"
}
```

### Authentication Errors

If authentication fails:

**Response**:
```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="Alert Orchestrator"
```

**Body**: (may be empty or contain error message)

---

## Code Examples

### Python

#### Fetch Decisions

```python
import requests

# Get decisions
response = requests.get('http://localhost:9808/api/decisions')
decisions = response.json()

# Check if DJED withdrawals allowed
if decisions['djed']['decision'] == 1:
    wmax = decisions['djed']['wmax_usd']
    print(f"DJED withdrawals OK: W_max = ${wmax:.2f}")
else:
    print("DJED withdrawals on HOLD")

# Check residual gate
if decisions['djed']['residual_trigger'] == 1:
    print("Residual gate triggered")
```

#### Trigger Transaction Sync (with Auth)

```python
import requests
from requests.auth import HTTPBasicAuth

# Trigger sync
response = requests.post(
    'http://localhost:9808/api/sync/transactions',
    auth=HTTPBasicAuth('admin', 'password')
)

result = response.json()
print(f"Deposits inserted: {result['deposits_inserted']}")
print(f"Withdrawals inserted: {result['withdrawals_inserted']}")

if result['errors']:
    print("Errors:")
    for error in result['errors']:
        print(f"  - {error}")
```

#### Monitor Metrics with Prometheus Client

```python
from prometheus_client.parser import text_string_to_metric_families
import requests

# Fetch metrics
response = requests.get('http://localhost:9808/metrics')
metrics_text = response.text

# Parse metrics
for family in text_string_to_metric_families(metrics_text):
    for sample in family.samples:
        if sample.name == 'wo_decision':
            asset = sample.labels['asset']
            decision = sample.value
            print(f"{asset}: decision={decision}")
```

### Bash/curl

#### Check Decision Status

```bash
#!/bin/bash

# Fetch decisions
DECISIONS=$(curl -s http://localhost:9808/api/decisions)

# Extract DJED decision
DJED_DECISION=$(echo "$DECISIONS" | jq -r '.djed.decision')

if [ "$DJED_DECISION" == "1" ]; then
  echo "DJED: WITHDRAW_OK"
elif [ "$DJED_DECISION" == "0" ]; then
  echo "DJED: HOLD"
else
  echo "DJED: ERROR"
fi
```

#### Scrape Metrics

```bash
#!/bin/bash

# Fetch all metrics
curl -s http://localhost:9808/metrics > metrics.txt

# Extract specific metric
grep "wo_wmax_usd" metrics.txt
```

#### Trigger Sync with Error Handling

```bash
#!/bin/bash

# Trigger sync
RESPONSE=$(curl -s -X POST -u admin:password \
  http://localhost:9808/api/sync/transactions)

# Check status
STATUS=$(echo "$RESPONSE" | jq -r '.status')

if [ "$STATUS" == "success" ]; then
  DEPOSITS=$(echo "$RESPONSE" | jq -r '.deposits_inserted')
  WITHDRAWALS=$(echo "$RESPONSE" | jq -r '.withdrawals_inserted')
  echo "Sync succeeded: $DEPOSITS deposits, $WITHDRAWALS withdrawals"
else
  echo "Sync failed:"
  echo "$RESPONSE" | jq -r '.errors[]'
  exit 1
fi
```

### JavaScript (Fetch API)

#### Fetch Decisions from Dashboard

```javascript
// Fetch decisions
fetch('http://localhost:9808/api/decisions')
  .then(response => response.json())
  .then(decisions => {
    // Process DJED decision
    const djed = decisions.djed;
    
    console.log(`DJED Decision: ${djed.decision}`);
    console.log(`W_max: $${djed.wmax_usd.toFixed(2)}`);
    
    // Display per-wallet breakdown
    djed.wallet_breakdown.forEach(wallet => {
      console.log(`  ${wallet.abbreviated_address}: $${wallet.wmax_usd.toFixed(2)}`);
    });
    
    // Check residual gate
    if (djed.residual_trigger === 1) {
      console.warn('Residual gate triggered!');
    }
  })
  .catch(error => console.error('Error fetching decisions:', error));
```

#### Auto-Refresh Dashboard Data

```javascript
function updateDashboard() {
  fetch('http://localhost:9808/api/decisions')
    .then(response => response.json())
    .then(decisions => {
      // Update UI elements
      document.getElementById('djed-wmax').textContent = 
        `$${decisions.djed.wmax_usd.toFixed(2)}`;
      
      // Update status badge
      const badge = document.getElementById('djed-status');
      if (decisions.djed.decision === 1) {
        badge.className = 'badge badge-success';
        badge.textContent = 'WITHDRAW_OK';
      } else if (decisions.djed.decision === 0) {
        badge.className = 'badge badge-warning';
        badge.textContent = 'HOLD';
      } else {
        badge.className = 'badge badge-danger';
        badge.textContent = 'ERROR';
      }
    });
}

// Auto-refresh every 10 seconds
setInterval(updateDashboard, 10000);
updateDashboard();  // Initial load
```

---

## Rate Limiting

**Current Implementation**: No rate limiting

**Recommendations**:
- Use reverse proxy (nginx, Caddy) for rate limiting
- Or implement application-level rate limiting if needed

---

## CORS (Cross-Origin Resource Sharing)

**Current Implementation**: No CORS headers

**If Needed**: Add CORS headers in reverse proxy configuration

**Example (nginx)**:
```nginx
location /api/ {
    proxy_pass http://localhost:9808;
    add_header Access-Control-Allow-Origin *;
    add_header Access-Control-Allow-Methods "GET, POST, OPTIONS";
}
```

---

## Versioning

**Current Version**: API v1 (implicit)

**Stability**: API is stable for v2.0 release

**Breaking Changes**: Will be announced in [CHANGELOG.md](CHANGELOG.md)

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)
