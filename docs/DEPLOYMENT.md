# Alert Orchestrator Deployment Guide

This guide covers production deployment, security hardening, monitoring setup, and maintenance procedures.

## Table of Contents

1. [Deployment Options](#deployment-options)
2. [Production Configuration](#production-configuration)
3. [Security Hardening](#security-hardening)
4. [Process Management](#process-management)
5. [Reverse Proxy Setup](#reverse-proxy-setup)
6. [Monitoring & Alerting](#monitoring--alerting)
7. [Backup & Recovery](#backup--recovery)
8. [Maintenance](#maintenance)
9. [Scaling Considerations](#scaling-considerations)

---

## Deployment Options

### Option 1: systemd Service (Recommended for Linux)

**Pros**:
- Native Linux process management
- Auto-restart on failure
- Log aggregation with journald
- Resource limits and isolation

**Cons**:
- Linux-only
- Requires root for setup

### Option 2: Docker Container

**Pros**:
- Portable across platforms
- Easy rollback
- Isolated environment
- Resource limits built-in

**Cons**:
- Docker overhead
- Configuration bind-mounts needed

### Option 3: Kubernetes

**Pros**:
- High availability
- Auto-scaling
- Rolling updates
- Service mesh integration

**Cons**:
- Complex setup
- Overkill for single instance

### Option 4: Manual Process

**Pros**:
- Simple for development
- Quick testing

**Cons**:
- No auto-restart
- Manual monitoring required
- Not recommended for production

---

## Production Configuration

### Minimal Production Config

```yaml
# config/orchestrator_config.yaml (production)

client:
  greptime:
    host: "http://greptime-prod.internal"
    port: 4000
    database: "liqwid"
    timeout: 30  # Increased for production
  
  assets:
    - "djed"
    - "usdm"
    - "wanusdc"
    - "wanusdt"
  
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
    listen_address: "127.0.0.1"  # Localhost only (use reverse proxy)
    listen_port: 9808
    path: "/metrics"
    metric_prefix: "wo_"
    
    expose:
      decision: true
      wmax_usd: true
      g_usd: true
      residual_usd: true
      sigma_usd: false  # Hide internal metrics
      k_sigma: false
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
    apply_in_fallback: true
  
  diagnostics:
    enabled: true
    dir: "/var/lib/alert-orchestrator/output"  # Persistent storage
    include_sigma_band: true
    include_k_sigma_band: true
  
  output_cleanup:
    enabled: true
    expire_before_relative: "7d"  # Auto-cleanup old charts
    paths:
      - "/var/lib/alert-orchestrator/output"
    extensions:
      - ".png"
  
  transaction_sync:
    enabled: false  # Manual trigger only
  
  apis:
    liqwid_graphql: "https://api.liqwid.finance/graphql"
    minswap_aggregator: "https://aggregator-api.minswap.org"
  
  auth:
    enabled: true
    username: "admin"
    password_hash: "bcrypt:$2b$12$..." # Use bcrypt hash
```

### Environment-Specific Configs

**Development**:
```yaml
orchestrator:
  schedule:
    interval_minutes: 5  # Faster iterations
  
  telemetry:
    listen_address: "0.0.0.0"  # Accessible from network
  
  diagnostics:
    dir: "output"  # Local directory
```

**Staging**:
```yaml
client:
  greptime:
    database: "liqwid_staging"  # Separate database

orchestrator:
  schedule:
    interval_minutes: 30
  
  auth:
    enabled: true  # Same as production
```

**Production**:
```yaml
client:
  greptime:
    database: "liqwid"
    timeout: 30

orchestrator:
  telemetry:
    listen_address: "127.0.0.1"  # Reverse proxy required
  
  auth:
    enabled: true
    username: "${ORCHESTRATOR_USERNAME}"  # Environment variables
    password_hash: "${ORCHESTRATOR_PASSWORD_HASH}"
```

---

## Security Hardening

### 1. Network Security

**Bind to Localhost Only**:
```yaml
orchestrator:
  telemetry:
    listen_address: "127.0.0.1"  # Not 0.0.0.0
    listen_port: 9808
```

Use reverse proxy (nginx, Caddy) with TLS for external access.

**Firewall Rules** (iptables):
```bash
# Allow only localhost connections to orchestrator port
sudo iptables -A INPUT -i lo -p tcp --dport 9808 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 9808 -j DROP

# Allow HTTPS for reverse proxy
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
```

**Firewall Rules** (firewalld):
```bash
# Block direct access to orchestrator port
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=127.0.0.1 port port=9808 protocol=tcp accept'
sudo firewall-cmd --reload
```

### 2. Authentication

**Enable Basic Auth**:
```yaml
orchestrator:
  auth:
    enabled: true
    username: "admin"
    password_hash: "bcrypt:$2b$12$..."  # Use bcrypt, not sha256
```

**Generate bcrypt hash** (Python):
```python
import bcrypt

password = b"your_secure_password"
hashed = bcrypt.hashpw(password, bcrypt.gensalt())
print(f"bcrypt:{hashed.decode()}")
```

**Protected Endpoints**:
- `/api/config/normalized` (exposes sensitive config)
- `/api/sync/transactions` (writes to database)

### 3. TLS/SSL

Use reverse proxy with TLS certificates (Let's Encrypt).

**nginx with certbot**:
```bash
sudo certbot --nginx -d orchestrator.example.com
```

**Caddy** (automatic HTTPS):
```caddy
orchestrator.example.com {
    reverse_proxy localhost:9808
}
```

### 4. File Permissions

**Set restrictive permissions**:
```bash
# Config file (contains sensitive info)
chmod 600 config/orchestrator_config.yaml
chown orchestrator:orchestrator config/orchestrator_config.yaml

# Output directory
chmod 750 /var/lib/alert-orchestrator/output
chown orchestrator:orchestrator /var/lib/alert-orchestrator/output

# Token registry
chmod 600 config/token_registry.csv
```

### 5. Secrets Management

**Use environment variables**:
```yaml
orchestrator:
  auth:
    username: "${ORCHESTRATOR_USERNAME}"
    password_hash: "${ORCHESTRATOR_PASSWORD_HASH}"
  
  apis:
    liqwid_graphql: "${LIQWID_API_URL}"
```

**Set via systemd service**:
```ini
[Service]
Environment="ORCHESTRATOR_USERNAME=admin"
Environment="ORCHESTRATOR_PASSWORD_HASH=bcrypt:..."
EnvironmentFile=/etc/alert-orchestrator/secrets.env
```

**Or use secrets manager** (e.g., HashiCorp Vault, AWS Secrets Manager)

### 6. Principle of Least Privilege

**Run as non-root user**:
```bash
sudo useradd -r -s /bin/false orchestrator
sudo chown -R orchestrator:orchestrator /opt/alert-orchestrator
```

**Restrict capabilities** (systemd):
```ini
[Service]
User=orchestrator
Group=orchestrator
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/alert-orchestrator
```

---

## Process Management

### systemd Service (Linux)

#### 1. Create Service File

```bash
sudo nano /etc/systemd/system/alert-orchestrator.service
```

**Service Definition**:
```ini
[Unit]
Description=Alert Orchestrator
After=network.target greptime.service
Wants=greptime.service

[Service]
Type=simple
User=orchestrator
Group=orchestrator
WorkingDirectory=/opt/alert-orchestrator

# Environment
Environment="PYTHONUNBUFFERED=1"
Environment="MPLBACKEND=Agg"
EnvironmentFile=/etc/alert-orchestrator/secrets.env

# Command
ExecStart=/opt/alert-orchestrator/venv/bin/python -m src.main \
  --config /etc/alert-orchestrator/config.yaml \
  --log-level INFO

# Restart policy
Restart=always
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

# Resource limits
MemoryLimit=512M
CPUQuota=50%

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/alert-orchestrator

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=alert-orchestrator

[Install]
WantedBy=multi-user.target
```

#### 2. Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable alert-orchestrator

# Start service
sudo systemctl start alert-orchestrator

# Check status
sudo systemctl status alert-orchestrator
```

#### 3. View Logs

```bash
# Tail logs
sudo journalctl -u alert-orchestrator -f

# View recent logs
sudo journalctl -u alert-orchestrator -n 100

# View logs since date
sudo journalctl -u alert-orchestrator --since "2025-01-21"
```

#### 4. Control Service

```bash
# Stop service
sudo systemctl stop alert-orchestrator

# Restart service
sudo systemctl restart alert-orchestrator

# Reload config (requires restart)
sudo systemctl reload alert-orchestrator  # Not supported, use restart

# Disable auto-start
sudo systemctl disable alert-orchestrator
```

### Docker Container

#### 1. Create Dockerfile

```dockerfile
# Dockerfile
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY config/ config/

# Create output directory
RUN mkdir -p /var/lib/orchestrator/output

# Set non-root user
RUN useradd -r -s /bin/false orchestrator && \
    chown -R orchestrator:orchestrator /app /var/lib/orchestrator
USER orchestrator

# Expose port
EXPOSE 9808

# Set environment
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg

# Run orchestrator
CMD ["python", "-m", "src.main", \
     "--config", "/app/config/orchestrator_config.yaml"]
```

#### 2. Build Image

```bash
docker build -t alert-orchestrator:latest .
```

#### 3. Run Container

```bash
docker run -d \
  --name orchestrator \
  --restart unless-stopped \
  -p 127.0.0.1:9808:9808 \
  -v /opt/orchestrator/config:/app/config:ro \
  -v /opt/orchestrator/output:/var/lib/orchestrator/output \
  -e ORCHESTRATOR_USERNAME=admin \
  -e ORCHESTRATOR_PASSWORD_HASH="bcrypt:..." \
  --memory 512m \
  --cpus 0.5 \
  alert-orchestrator:latest
```

#### 4. Manage Container

```bash
# View logs
docker logs -f orchestrator

# Stop container
docker stop orchestrator

# Start container
docker start orchestrator

# Restart container
docker restart orchestrator

# Remove container
docker rm -f orchestrator
```

### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  orchestrator:
    build: .
    container_name: alert-orchestrator
    restart: unless-stopped
    ports:
      - "127.0.0.1:9808:9808"
    volumes:
      - ./config:/app/config:ro
      - orchestrator-output:/var/lib/orchestrator/output
    environment:
      - ORCHESTRATOR_USERNAME=admin
      - ORCHESTRATOR_PASSWORD_HASH=${ORCHESTRATOR_PASSWORD_HASH}
    env_file:
      - .env
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'

volumes:
  orchestrator-output:
```

**Run with Compose**:
```bash
docker-compose up -d
docker-compose logs -f
docker-compose down
```

---

## Reverse Proxy Setup

### nginx

#### Configuration

```nginx
# /etc/nginx/sites-available/orchestrator

upstream orchestrator {
    server 127.0.0.1:9808;
}

server {
    listen 80;
    server_name orchestrator.example.com;
    
    # Redirect HTTP to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name orchestrator.example.com;
    
    # SSL certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/orchestrator.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/orchestrator.example.com/privkey.pem;
    
    # SSL security
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    # Access logs
    access_log /var/log/nginx/orchestrator-access.log;
    error_log /var/log/nginx/orchestrator-error.log;
    
    # Dashboard (public)
    location / {
        proxy_pass http://orchestrator;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (if needed in future)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # Metrics (restrict to Prometheus server)
    location /metrics {
        proxy_pass http://orchestrator;
        
        # Allow only Prometheus server
        allow 192.168.1.100;  # Prometheus IP
        deny all;
    }
    
    # Protected API endpoints (require auth)
    location /api/config/normalized {
        proxy_pass http://orchestrator;
        
        # Already has Basic Auth in app, but can add nginx auth too
        auth_basic "Orchestrator Admin";
        auth_basic_user_file /etc/nginx/.htpasswd;
    }
    
    location /api/sync/transactions {
        proxy_pass http://orchestrator;
        auth_basic "Orchestrator Admin";
        auth_basic_user_file /etc/nginx/.htpasswd;
    }
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=orchestrator:10m rate=10r/s;
    limit_req zone=orchestrator burst=20;
}
```

#### Enable Site

```bash
# Create .htpasswd for nginx auth
sudo htpasswd -c /etc/nginx/.htpasswd admin

# Enable site
sudo ln -s /etc/nginx/sites-available/orchestrator /etc/nginx/sites-enabled/

# Test config
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

### Caddy

#### Caddyfile

```caddy
# /etc/caddy/Caddyfile

orchestrator.example.com {
    # Automatic HTTPS
    
    # Reverse proxy to orchestrator
    reverse_proxy localhost:9808
    
    # Rate limiting (requires plugin)
    rate_limit {
        zone orchestrator
        rate 10
        burst 20
    }
    
    # Restrict /metrics to Prometheus
    @metrics {
        path /metrics
        not remote_ip 192.168.1.100  # Prometheus IP
    }
    route @metrics {
        error 403
    }
    
    # Logs
    log {
        output file /var/log/caddy/orchestrator-access.log
    }
}
```

#### Reload Caddy

```bash
sudo systemctl reload caddy
```

---

## Monitoring & Alerting

### Prometheus

#### Configuration

```yaml
# prometheus.yml

global:
  scrape_interval: 60s
  evaluation_interval: 60s

scrape_configs:
  - job_name: 'alert_orchestrator'
    static_configs:
      - targets: ['localhost:9808']
    metrics_path: '/metrics'
    scrape_interval: 60s
    scrape_timeout: 10s
```

#### Restart Prometheus

```bash
sudo systemctl restart prometheus
```

### Grafana Dashboards

#### Import Dashboard JSON

Save the following as `orchestrator-dashboard.json`:

```json
{
  "dashboard": {
    "title": "Alert Orchestrator",
    "panels": [
      {
        "id": 1,
        "title": "Decision Status",
        "type": "stat",
        "targets": [
          {
            "expr": "wo_decision{asset=\"djed\"}"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "value": -1, "color": "red" },
                { "value": 0, "color": "orange" },
                { "value": 1, "color": "green" }
              ]
            },
            "mappings": [
              { "type": "value", "value": "-1", "text": "ERROR" },
              { "type": "value", "value": "0", "text": "HOLD" },
              { "type": "value", "value": "1", "text": "WITHDRAW_OK" }
            ]
          }
        }
      },
      {
        "id": 2,
        "title": "W_max (USD)",
        "type": "graph",
        "targets": [
          {
            "expr": "wo_wmax_usd"
          }
        ]
      },
      {
        "id": 3,
        "title": "Corrected Gains (USD)",
        "type": "graph",
        "targets": [
          {
            "expr": "wo_g_usd"
          }
        ]
      },
      {
        "id": 4,
        "title": "Residual Gating",
        "type": "graph",
        "targets": [
          {
            "expr": "wo_residual_usd",
            "legendFormat": "Residual"
          },
          {
            "expr": "wo_sigma_usd * wo_k_sigma",
            "legendFormat": "Threshold"
          }
        ]
      }
    ]
  }
}
```

Import in Grafana: **Dashboards** → **Import** → Upload JSON

### Alertmanager

#### Alert Rules

```yaml
# /etc/prometheus/rules/orchestrator.yml

groups:
  - name: alert_orchestrator
    interval: 60s
    rules:
      # Alert when residual gate triggered
      - alert: ResidualGateTriggered
        expr: wo_residual_trigger == 1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Residual gate triggered for {{ $labels.asset }}"
          description: "Asset {{ $labels.asset }} has residual gate active (|r(t1)| > k*σ)"
      
      # Alert when W_max drops to zero unexpectedly
      - alert: WmaxDroppedToZero
        expr: wo_wmax_usd == 0 and wo_decision == 0
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "W_max dropped to zero for {{ $labels.asset }}"
          description: "Asset {{ $labels.asset }} has W_max=0, check gains or residual trigger"
      
      # Alert when evaluation fails
      - alert: EvaluationError
        expr: wo_decision == -1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Evaluation error for {{ $labels.asset }}"
          description: "Asset {{ $labels.asset }} evaluation failed (decision=-1)"
      
      # Alert when orchestrator stops evaluating
      - alert: OrchestratorStale
        expr: time() - wo_last_eval_timestamp_seconds > 3600
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Orchestrator not evaluating"
          description: "Last evaluation was {{ $value | humanizeDuration }} ago"
```

#### Alertmanager Config

```yaml
# /etc/alertmanager/alertmanager.yml

route:
  group_by: ['alertname', 'asset']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 12h
  receiver: 'slack'

receivers:
  - name: 'slack'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL'
        channel: '#orchestrator-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'
```

---

## Backup & Recovery

### Configuration Backup

```bash
#!/bin/bash
# backup-config.sh

BACKUP_DIR="/var/backups/orchestrator"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# Backup config
tar -czf "$BACKUP_DIR/config_$DATE.tar.gz" \
  /etc/alert-orchestrator/config.yaml \
  /etc/alert-orchestrator/token_registry.csv \
  /etc/alert-orchestrator/secrets.env

# Keep only last 30 days
find "$BACKUP_DIR" -name "config_*.tar.gz" -mtime +30 -delete

echo "Config backed up to $BACKUP_DIR/config_$DATE.tar.gz"
```

**Run daily**:
```bash
sudo crontab -e
# Add:
0 2 * * * /usr/local/bin/backup-config.sh
```

### Disaster Recovery

**Restore from backup**:
```bash
# Stop service
sudo systemctl stop alert-orchestrator

# Restore config
tar -xzf /var/backups/orchestrator/config_20250121_020000.tar.gz -C /

# Verify config
python -m src.main --print-config-normalized

# Start service
sudo systemctl start alert-orchestrator
```

---

## Maintenance

### Regular Tasks

**Daily**:
- Check service status: `systemctl status alert-orchestrator`
- Review logs for errors: `journalctl -u alert-orchestrator --since today`
- Verify evaluations running: Check `wo_last_eval_timestamp_seconds` metric

**Weekly**:
- Review dashboard for trends
- Check output cleanup working: `ls -lh /var/lib/alert-orchestrator/output`
- Update token registry if new assets added

**Monthly**:
- Review configuration (safety factor, gate thresholds)
- Analyze false positive rate for residual gate
- Check for software updates
- Backup configuration

### Updates

**Update orchestrator**:
```bash
# Pull latest code
cd /opt/alert-orchestrator
git pull

# Activate venv
source venv/bin/activate

# Update dependencies
pip install -r requirements.txt

# Restart service
sudo systemctl restart alert-orchestrator
```

**Rolling update (zero downtime)**:
1. Deploy new version to staging
2. Test thoroughly
3. Update production (service restart required)

---

## Scaling Considerations

### Single Instance (Current)

**Suitable For**:
- < 20 assets
- Evaluation interval ≥ 30 minutes
- < 1000 req/min to dashboard

**Limitations**:
- No high availability
- Single point of failure

### High Availability (Future)

**Options**:
- **Active-Passive**: Use keepalived for failover
- **Load Balanced**: Multiple instances behind load balancer (metrics aggregation needed)
- **Kubernetes**: Deploy as ReplicaSet with service

**Challenges**:
- Prometheus metrics federation
- Coordinated evaluations (prevent duplicate work)

---

**Last Updated**: 2025-01-21  
**Version**: 2.0 (Phase B - Residual Gating)
