# Security Audit Report - Docker Image
## Alert Orchestrator - Sensitive Data Check

**Date**: October 21, 2025
**Audit Focus**: Identifying sensitive information in Docker image

---

## Executive Summary

✅ **SAFE TO PUBLISH** - The Docker image does NOT contain critical personal information.

---

## Detailed Findings

### 🔒 What IS Included in the Docker Image

1. **Application Source Code** (`src/`)
   - ✅ Safe: No hardcoded credentials, wallet addresses, or private keys
   - ✅ Safe: Only business logic and data processing code

2. **Default Configuration Files**
   ```
   config/orchestrator_config.yaml
   config/token_registry.csv
   ```
   
   **Analysis**:
   - ⚠️ **CONTAINS**: Your private network IP address: `192.168.1.12` (GreptimeDB host)
   - ✅ **Safe**: Policy IDs and token hex names (public blockchain data)
   - ✅ **Safe**: No wallet addresses
   - ✅ **Safe**: No passwords or API keys
   - ✅ **Safe**: `auth.enabled: true` but NO credentials stored

### 🚫 What is NOT Included

- ✅ **Excluded by `.dockerignore`**:
  - `tests/` (test wallet addresses are fake examples like `addr1q123...`)
  - `output/` (runtime data)
  - `docs/` (documentation)
  - `.git/` (version control history)
  - Log files and debug output

- ✅ **Never in codebase**:
  - No real wallet addresses
  - No private keys or mnemonics
  - No passwords
  - No API authentication tokens

---

## Security Concerns & Recommendations

### ⚠️ CONCERN: Private IP Address Exposed

**What's exposed**: `192.168.1.12` (your GreptimeDB server IP)

**Risk Level**: 🟡 **LOW** 
- Private RFC1918 IP address (192.168.x.x)
- Only accessible within your local network
- Not routable on the internet
- Doesn't reveal personal information

**Recommendation**: 
✅ **ACCEPTABLE** for Docker Hub publication, but consider:

1. **Option A**: Use environment variable override (recommended)
2. **Option B**: Document that users should customize config
3. **Option C**: Use placeholder in default config

---

## Recommendations

### 1. Remove Default Config from Image (Recommended)

**Current Dockerfile** (lines 51-52):
```dockerfile
# Copy default configuration files (can be overridden via volume mounts)
COPY --chown=orchestrator:orchestrator config/orchestrator_config.yaml ./config/
COPY --chown=orchestrator:orchestrator config/token_registry.csv ./config/
```

**Recommended Change**:
```dockerfile
# Create empty config directory (configs provided via volume mounts)
# Users MUST mount their own config files
```

**Benefits**:
- ✅ No private IP in public image
- ✅ Forces users to provide their own config
- ✅ Better security practice
- ✅ No accidental exposure of any local details

### 2. Alternative: Use Placeholder Config

Keep default config but use placeholder values:
```yaml
data:
  databases:
    greptime:
      host: "http://YOUR_GREPTIME_HOST"  # Change this
      port: 7010
```

### 3. Update docker-compose.yaml

Already correct! Volume mounts override the default:
```yaml
volumes:
  - ./config/orchestrator_config.yaml:/app/config/orchestrator_config.yaml:ro
```

This means users provide their own config, the default in the image is never used.

---

## Token Registry - Public Data

The `token_registry.csv` contains:
```csv
asset,policy_id,token_name_hex
djed,8db269c3ec630e06ae29f74bc39edd1f87c819f1056206e879a1cd61,446a65644d6963726f555344
...
```

✅ **SAFE**: This is PUBLIC blockchain data
- Policy IDs are public (on-chain identifiers)
- Token names are public
- No personal information
- Anyone can look these up on CardanoScan or similar explorers

---

## Test Data Review

Test files contain fake wallet addresses like:
```python
wallet_address="addr1qxytz12345678901234567890abcdef5ur5m"  # FAKE
```

✅ **SAFE**: These are example addresses for unit tests
- Not real wallets
- Excluded from Docker image anyway (`.dockerignore` excludes `tests/`)

---

## Final Verdict

### ✅ SAFE TO PUBLISH with minor recommendation

**Current Risk**: 🟢 **MINIMAL**
- Only exposes a private IP (192.168.1.12) which is not sensitive
- No credentials, wallets, or personal data
- Users override config with volume mounts anyway

**Recommendation Priority**:
1. 🟡 **Low Priority**: Remove default config from image (best practice)
2. 🟢 **Optional**: Add placeholder values in default config
3. ✅ **Already Good**: Volume mount strategy in docker-compose.yaml

---

## Action Items

### Option 1: Remove Default Config from Image (Most Secure)

1. Update Dockerfile to NOT copy config files
2. Document that users MUST provide config via volume mount
3. Add validation on startup if config is missing

### Option 2: Keep As-Is (Acceptable)

- Private IP exposure is minimal risk
- Volume mounts override defaults anyway
- Add note in README that default config should not be used

### Option 3: Use Placeholder Config

- Replace `192.168.1.12` with `YOUR_GREPTIME_HOST`
- Add clear comments in config file
- Users must edit before use

---

## Conclusion

**The Docker image is SAFE to publish to Docker Hub.**

The only "sensitive" data is a private network IP address (192.168.1.12), which:
- Is not routable on the internet
- Doesn't reveal personal information  
- Is overridden by user's volume-mounted config anyway

No wallet addresses, passwords, API keys, or truly sensitive data exists in the image.

**Recommended**: Implement Option 1 or Option 3 before publishing for best security practice, but current state is acceptable for immediate deployment.

---

## Checklist Before Publishing

- [x] No hardcoded passwords ✅
- [x] No API keys or tokens ✅
- [x] No wallet addresses ✅
- [x] No private keys or mnemonics ✅
- [x] No personal identifying information ✅
- [x] Test data excluded from image ✅
- [ ] Consider removing default config (recommended but optional)

**Status**: 🟢 **APPROVED FOR PUBLICATION**
