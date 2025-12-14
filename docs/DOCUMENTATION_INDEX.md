
# Alert Orchestrator Documentation Index

Welcome to the Alert Orchestrator documentation. This index will help you find the information you need quickly.

## 📚 Documentation Structure

### Getting Started
- **[README.md](README.md)** - Project overview, quick start, and feature summary
- **[GETTING_STARTED.md](GETTING_STARTED.md)** - Step-by-step setup guide for new users
- **[CONFIGURATION.md](CONFIGURATION.md)** - Complete configuration reference

### Core Documentation
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design, components, and data flows
- **[API.md](API.md)** - HTTP API reference for all endpoints
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Common issues and solutions

### Operations
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Production deployment, security, and monitoring
- **[DOCKER_COMPLETE_GUIDE.md](DOCKER_COMPLETE_GUIDE.md)** - Complete Docker deployment guide (all platforms)
- **[DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)** - Docker basics and quick start
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and release notes

---

## 🎯 Quick Navigation

### I want to...

#### **Get started from scratch**
1. Read [README.md](README.md) - Overview
2. Follow [GETTING_STARTED.md](GETTING_STARTED.md) - Setup steps
3. Review [CONFIGURATION.md](CONFIGURATION.md) - Configure for your environment

#### **Understand how it works**
1. Read [ARCHITECTURE.md](ARCHITECTURE.md) - System design
2. Review [Decision Logic](ARCHITECTURE.md#decision-logic) - How decisions are made
3. Review [Data Flow](ARCHITECTURE.md#data-flow) - How data moves through the system

#### **Deploy to production**
1. Read [DEPLOYMENT.md](DEPLOYMENT.md) - Deployment options
2. For Docker: Read [DOCKER_COMPLETE_GUIDE.md](DOCKER_COMPLETE_GUIDE.md) - Complete Docker guide
3. Follow [Security Hardening](DEPLOYMENT.md#security-hardening) - Secure your deployment
4. Set up [Monitoring & Alerting](DEPLOYMENT.md#monitoring--alerting) - Monitor your instance

#### **Deploy with Docker**
1. Read [DOCKER_COMPLETE_GUIDE.md](DOCKER_COMPLETE_GUIDE.md) - Complete Docker guide
2. For Synology NAS: Follow [Synology NAS Deployment](DOCKER_COMPLETE_GUIDE.md#synology-nas-deployment)
3. For Kubernetes: Follow [Kubernetes Deployment](DOCKER_COMPLETE_GUIDE.md#kubernetes-deployment)
4. Review [Troubleshooting](DOCKER_COMPLETE_GUIDE.md#troubleshooting) - Docker-specific issues

#### **Integrate with my application**
1. Read [API.md](API.md) - API reference
2. Review [Code Examples](API.md#code-examples) - Integration examples
3. Check [Response Formats](API.md#response-formats) - Data structures

#### **Troubleshoot an issue**
1. Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
2. Review [Common Error Messages](TROUBLESHOOTING.md#common-error-messages) - Error reference
3. Use [Diagnostic Tools](TROUBLESHOOTING.md#diagnostic-tools) - Debug tools

#### **Configure a feature**
1. Find feature in [CONFIGURATION.md](CONFIGURATION.md) - Configuration reference
2. Review examples and defaults
3. Validate with `--print-config-normalized` flag

#### **Learn about a specific component**
1. Find component in [ARCHITECTURE.md](ARCHITECTURE.md#core-components)
2. Review source code in `src/core/` or `src/shared/`
3. Check API interactions in [API.md](API.md)

---

## 📖 Documentation by Topic

### Configuration

| Topic | Document | Section |
|-------|----------|---------|
| YAML structure | [CONFIGURATION.md](CONFIGURATION.md) | Top-level keys |
| GreptimeDB connection | [CONFIGURATION.md](CONFIGURATION.md) | client.greptime |
| Asset configuration | [CONFIGURATION.md](CONFIGURATION.md) | client.assets |
| Reference keyword | [CONFIGURATION.md](CONFIGURATION.md) | orchestrator.reference_keyword |
| Safety factor | [CONFIGURATION.md](CONFIGURATION.md) | orchestrator.safety_factor |
| Residual gating | [CONFIGURATION.md](CONFIGURATION.md) | orchestrator.decision_gate |
| Diagnostics | [CONFIGURATION.md](CONFIGURATION.md) | orchestrator.diagnostics |
| Telemetry | [CONFIGURATION.md](CONFIGURATION.md) | orchestrator.telemetry |
| Per-asset overrides | [CONFIGURATION.md](CONFIGURATION.md) | decision_gate.per_asset |

### Architecture

| Topic | Document | Section |
|-------|----------|---------|
| System overview | [ARCHITECTURE.md](ARCHITECTURE.md) | System Overview |
| Entry point | [ARCHITECTURE.md](ARCHITECTURE.md) | Entry Point (main.py) |
| Decision logic | [ARCHITECTURE.md](ARCHITECTURE.md) | Decision Logic (alert_logic.py) |
| HTTP server | [ARCHITECTURE.md](ARCHITECTURE.md) | HTTP Server & Metrics (exporter.py) |
| Diagnostics | [ARCHITECTURE.md](ARCHITECTURE.md) | Diagnostics (diagnostics.py) |
| Configuration system | [ARCHITECTURE.md](ARCHITECTURE.md) | Configuration Management (settings.py) |
| Data flow | [ARCHITECTURE.md](ARCHITECTURE.md) | Data Flow |
| Price sources | [ARCHITECTURE.md](ARCHITECTURE.md) | Price Sources |
| Residual gating | [ARCHITECTURE.md](ARCHITECTURE.md) | Residual Gating |
| Database schema | [ARCHITECTURE.md](ARCHITECTURE.md) | Database Schema |

### API

| Topic | Document | Section |
|-------|----------|---------|
| Endpoint overview | [API.md](API.md) | Endpoints |
| Authentication | [API.md](API.md) | Authentication |
| `/metrics` endpoint | [API.md](API.md) | GET /metrics |
| `/dashboard` endpoint | [API.md](API.md) | GET /dashboard |
| `/api/decisions` endpoint | [API.md](API.md) | GET /api/decisions |
| Response formats | [API.md](API.md) | Response Formats |
| Error handling | [API.md](API.md) | Error Handling |
| Code examples | [API.md](API.md) | Code Examples |

### Troubleshooting

| Topic | Document | Section |
|-------|----------|---------|
| Connection issues | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Connection Issues |
| Configuration problems | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Configuration Problems |
| Data issues | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Data Issues |
| Decision logic issues | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Decision Logic Issues |
| Dashboard issues | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Dashboard & Metrics Issues |
| Performance problems | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Performance Problems |
| Diagnostic tools | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Diagnostic Tools |

### Deployment

| Topic | Document | Section |
|-------|----------|---------|
| Deployment options | [DEPLOYMENT.md](DEPLOYMENT.md) | Deployment Options |
| Production config | [DEPLOYMENT.md](DEPLOYMENT.md) | Production Configuration |
| Security hardening | [DEPLOYMENT.md](DEPLOYMENT.md) | Security Hardening |
| systemd service | [DEPLOYMENT.md](DEPLOYMENT.md) | Process Management - systemd |
| Docker container | [DEPLOYMENT.md](DEPLOYMENT.md) | Process Management - Docker |
| Reverse proxy (nginx) | [DEPLOYMENT.md](DEPLOYMENT.md) | Reverse Proxy Setup - nginx |
| Prometheus setup | [DEPLOYMENT.md](DEPLOYMENT.md) | Monitoring & Alerting - Prometheus |
| Grafana dashboards | [DEPLOYMENT.md](DEPLOYMENT.md) | Monitoring & Alerting - Grafana |
| Backup & recovery | [DEPLOYMENT.md](DEPLOYMENT.md) | Backup & Recovery |

---

## 🔍 Search by Use Case

### Use Case: Monitor DJED withdrawals with conservative safety

**Goal**: Set up monitoring for DJED stablecoin with 50% safety factor.

**Documents**:
1. [GETTING_STARTED.md](GETTING_STARTED.md) - Initial setup
2. [CONFIGURATION.md](CONFIGURATION.md) - Configure safety factor
3. [DEPLOYMENT.md](DEPLOYMENT.md) - Deploy to production

**Configuration**:
```yaml
client:
  assets: ["djed"]

orchestrator:
  reference_keyword: "alert_driven_withdrawal"
  safety_factor:
    c: 0.5  # 50% safety factor
```

### Use Case: Enable residual gating to prevent anomalous withdrawals

**Goal**: Use statistical analysis to gate withdrawals during market anomalies.

**Documents**:
1. [ARCHITECTURE.md](ARCHITECTURE.md#residual-gating) - Understand residual gating
2. [CONFIGURATION.md](CONFIGURATION.md) - Configure decision gate
3. [TROUBLESHOOTING.md](TROUBLESHOOTING.md#residual-gate-always-triggered) - Tune thresholds

**Configuration**:
```yaml
orchestrator:
  decision_gate:
    enabled: true
    whitelist: ["djed"]
    k_sigma: 2.0  # 2-sigma threshold
```

### Use Case: Deploy with Prometheus and Grafana monitoring

**Goal**: Full production deployment with metrics and dashboards.

**Documents**:
1. [DEPLOYMENT.md](DEPLOYMENT.md#process-management) - Set up systemd service
2. [DEPLOYMENT.md](DEPLOYMENT.md#monitoring--alerting) - Configure Prometheus/Grafana
3. [API.md](API.md#get-metrics) - Understand metrics format

**Steps**:
1. Deploy orchestrator as systemd service
2. Configure Prometheus scraping
3. Import Grafana dashboard template
4. Set up Alertmanager rules

### Use Case: Integrate API into custom application

**Goal**: Fetch decisions programmatically from my application.

**Documents**:
1. [API.md](API.md#get-apidecisions) - API endpoint reference
2. [API.md](API.md#code-examples) - Integration examples
3. [ARCHITECTURE.md](ARCHITECTURE.md#decision-logic) - Understand decision output

**Example**:
```python
import requests

response = requests.get('http://localhost:9808/api/decisions')
decisions = response.json()

if decisions['djed']['decision'] == 1:
    wmax = decisions['djed']['wmax_usd']
    print(f"Safe to withdraw up to ${wmax:.2f}")
```

### Use Case: Troubleshoot "W_max always 0" issue

**Goal**: Fix issue where all assets show W_max = 0.

**Documents**:
1. [TROUBLESHOOTING.md](TROUBLESHOOTING.md#issue-w_max-always-0) - Diagnosis steps
2. [ARCHITECTURE.md](ARCHITECTURE.md#decision-flow) - Understand decision flow
3. [CONFIGURATION.md](CONFIGURATION.md) - Check configuration

**Common Causes**:
- Non-positive gains since reference point
- Residual gate triggered (threshold too low)
- No tagged withdrawal found (fallback mode = "null")

---

## 📊 Feature Matrix

| Feature | Version | Documentation | Config Key |
|---------|---------|---------------|------------|
| Core withdrawal advisor | 1.0.0+ | [ARCHITECTURE.md](ARCHITECTURE.md#decision-logic) | `orchestrator.safety_factor` |
| Residual gating | 2.0.0+ | [ARCHITECTURE.md](ARCHITECTURE.md#residual-gating) | `orchestrator.decision_gate` |
| Per-wallet W_max | 2.0.0+ | [ARCHITECTURE.md](ARCHITECTURE.md#decision-logic) | (automatic) |
| Diagnostic charts | 2.0.0+ | [ARCHITECTURE.md](ARCHITECTURE.md#diagnostics) | `orchestrator.diagnostics` |
| Prometheus metrics | 1.0.0+ | [API.md](API.md#get-metrics) | `orchestrator.telemetry` |
| Web dashboard | 1.0.0+ | [API.md](API.md#get-dashboard) | `orchestrator.telemetry` |
| Basic authentication | 2.0.0+ | [DEPLOYMENT.md](DEPLOYMENT.md#authentication) | `orchestrator.auth` |
| Price comparison | 2.0.0+ | [ARCHITECTURE.md](ARCHITECTURE.md#price-sources) | `orchestrator.apis` |
| Transaction sync | 2.0.0+ | [API.md](API.md#post-apisync-transactions) | `orchestrator.transaction_sync` |
| Output cleanup | 2.0.0+ | [CONFIGURATION.md](CONFIGURATION.md) | `orchestrator.output_cleanup` |

---

## 🆘 Getting Help

### Self-Service

1. **Search this documentation** - Use your browser's search (Ctrl+F / Cmd+F)
2. **Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Common issues and solutions
3. **Use diagnostic tools** - `--print-config-normalized`, `--once`, `--log-level DEBUG`
4. **Review logs** - Check application logs for error messages

### Community Support

1. **GitHub Issues** - Report bugs, request features
2. **GitHub Discussions** - Ask questions, share experiences
3. **Stack Overflow** - Tag: `alert-orchestrator` (if applicable)

### When Reporting Issues

Include:
- Orchestrator version (from [CHANGELOG.md](CHANGELOG.md))
- Configuration (sanitize sensitive data)
- Error messages from logs
- Steps to reproduce
- Expected vs actual behavior

---

## 🔄 Keeping Documentation Updated

This documentation was last updated for **version 2.0.0** (2025-01-21).

If you find:
- Outdated information
- Missing documentation
- Unclear explanations
- Broken links

Please:
1. Open a GitHub Issue describing the problem
2. Or submit a Pull Request with corrections
3. Tag with `documentation` label

---

## 📝 Documentation Standards

All documentation follows:
- **Markdown format** for readability and version control
- **Clear headings** for navigation
- **Code examples** for practical guidance
- **Tables** for quick reference
- **Links** for cross-referencing

---

## 🏗️ Contributing to Documentation

See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Documentation style guide
- How to propose changes
- Review process

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)  
**Documentation Maintainer**: [Your Team Name]
