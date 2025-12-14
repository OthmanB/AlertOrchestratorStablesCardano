# Docker Deployment - Complete Guide

This guide covers all Docker deployment scenarios for the Alert Orchestrator, including local development, Synology NAS, and Kubernetes.

## Table of Contents

- [Quick Start](#quick-start)
- [Deployment Options](#deployment-options)
- [Building Images](#building-images)
- [Synology NAS Deployment](#synology-nas-deployment)
- [Kubernetes Deployment](#kubernetes-deployment)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Pull Pre-Built Image

```bash
docker pull obenomar/alert-orchestrator:latest
```

### Run with Docker

```bash
docker run -d \
  --name alert-orchestrator \
  -p 9808:9808 \
  -v /path/to/config:/app/config:ro \
  -v /path/to/output:/app/output:rw \
  -e WO_BASIC_AUTH_USER=admin \
  -e WO_BASIC_AUTH_PASS=secure_password \
  obenomar/alert-orchestrator:latest
```

### Run with Docker Compose

```bash
cd alert_orchestrator
docker-compose up -d
```

---

## Deployment Options

### Option 1: Use Pre-Built Image (Recommended)

**Best for**: Quick deployment, production use

```bash
# Pull from Docker Hub
docker pull obenomar/alert-orchestrator:latest

# Run with docker-compose
docker-compose up -d
```

**Pros**:
- ✅ Fast deployment (no build time)
- ✅ Tested and verified image
- ✅ Works on x86_64 and ARM64 platforms
- ✅ Uses official NumPy wheels

**Cons**:
- ❌ Requires internet connection
- ❌ Trusts external image

---

### Option 2: Build on Development Machine

**Best for**: Development, testing, pushing to private registry

```bash
# Build for multiple platforms
./build-docker.sh --push --tag v1.0.0
```

**Pros**:
- ✅ Control over build process
- ✅ Multi-platform support (amd64, arm64)
- ✅ Can customize Dockerfile
- ✅ Push to your own registry

**Cons**:
- ❌ Requires Docker Buildx
- ❌ Build takes 3-5 minutes

---

### Option 3: Build on Target Device

**Best for**: Synology NAS, air-gapped environments, maximum compatibility

```bash
# On Synology NAS
sudo bash build-synology.sh
sudo docker-compose up -d
```

**Pros**:
- ✅ 100% architecture compatibility
- ✅ No registry required
- ✅ Works in air-gapped environments
- ✅ Matches target OS exactly

**Cons**:
- ❌ Slower build (5-10 minutes)
- ❌ Requires build tools on target
- ❌ More disk space needed

---

## Building Images

### Build Script 1: `build-docker.sh` (Multi-Platform)

**Purpose**: Build on Mac/Linux/Windows and push to Docker Hub

**Features**:
- Builds for linux/amd64 and linux/arm64
- Uses Docker Buildx
- Uses NumPy pre-built wheels (fast)
- Can push to Docker Hub

**Usage**:

```bash
# Build locally (for testing)
./build-docker.sh

# Build and push to Docker Hub
./build-docker.sh --push

# Build with custom tag
./build-docker.sh --tag v1.2.3 --push
```

**Requirements**:
- Docker Desktop or Docker with Buildx plugin
- Docker Hub login (for --push)

**When to Use**:
- Building on Mac/Windows/Linux development machine
- Need multi-platform images
- Pushing to Docker Hub or private registry
- CI/CD pipelines

---

### Build Script 2: `build-synology.sh` (Single-Platform)

**Purpose**: Build directly on Synology NAS or target deployment device

**Features**:
- Builds for local architecture only
- No Buildx required (uses standard `docker build`)
- Auto-detects "monitoring" user UID/GID
- Installs runtime dependencies

**Usage**:

```bash
# On Synology NAS (as admin)
sudo bash build-synology.sh
```

**Requirements**:
- Docker installed on target device
- SSH access (for Synology)
- Sudo privileges

**When to Use**:
- Deploying on Synology NAS
- NumPy compatibility issues with pre-built images
- Air-gapped environments
- Custom user UID/GID requirements

---

## Synology NAS Deployment

### Prerequisites

1. **Synology NAS** with Docker package installed
2. **SSH access** as admin user
3. **Monitoring user** created (UID 1033, GID 100)
4. **Config directory** prepared

### Step 1: Create Config Directory

```bash
# SSH as admin
ssh admin@synology-ip

# Create directories
sudo mkdir -p /volume2/docker-vol2/liqwid-alertmanager/config/
sudo mkdir -p /volume2/docker-vol2/liqwid-alertmanager/output/

# Set ownership to monitoring user
sudo chown -R 1033:100 /volume2/docker-vol2/liqwid-alertmanager/
sudo chmod -R 755 /volume2/docker-vol2/liqwid-alertmanager/
```

### Step 2: Transfer Files

**Option A: Using SCP (from Mac)**

```bash
# On your Mac
cd /path/to/alert_orchestrator

# Copy entire directory
tar -czf - . | ssh admin@synology-ip "cd /volume1/docker/alert_orchestrator && sudo tar -xzf -"

# Copy config files
scp config/orchestrator_config.yaml admin@synology-ip:/tmp/
scp config/token_registry.csv admin@synology-ip:/tmp/

# Move to final location
ssh admin@synology-ip "sudo mv /tmp/orchestrator_config.yaml /volume2/docker-vol2/liqwid-alertmanager/config/ && \
                        sudo mv /tmp/token_registry.csv /volume2/docker-vol2/liqwid-alertmanager/config/"
```

**Option B: Using Synology File Station**

1. Open File Station in browser
2. Navigate to `/docker/alert_orchestrator/`
3. Upload all files maintaining directory structure
4. Upload config files to `/docker-vol2/liqwid-alertmanager/config/`

### Step 3: Configure Authentication

```bash
# Edit docker-compose.yaml
sudo nano /volume1/docker/alert_orchestrator/docker-compose.yaml

# Change these lines:
# - WO_BASIC_AUTH_USER=your_username
# - WO_BASIC_AUTH_PASS=your_secure_password
```

### Step 4: Build Image

```bash
cd /volume1/docker/alert_orchestrator
sudo bash build-synology.sh
```

**Expected output**:
```
Building Alert Orchestrator on Synology
Detecting user permissions...
✓ Using monitoring user: UID=1033, GID=100
Building image for local architecture (x86_64)...
⏱ This will take 5-10 minutes...
✅ Build completed successfully
```

### Step 5: Deploy

```bash
sudo docker-compose up -d
```

### Step 6: Verify

```bash
# Check container status
sudo docker ps | grep alert-orchestrator

# Check logs
sudo docker logs -f alert-orchestrator

# Test endpoints
curl http://localhost:9808/metrics
curl -u username:password http://localhost:9808/dashboard
```

**Success indicators**:
- ✅ Container status: "Up X seconds"
- ✅ Logs show: "HTTP server started on port 9808"
- ✅ No NumPy import errors
- ✅ No permission denied errors

---

## Kubernetes Deployment

### Prerequisites

1. Kubernetes cluster (1.19+)
2. `kubectl` configured
3. Docker image pushed to registry

### Step 1: Create Namespace

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: alert-orchestrator
```

```bash
kubectl apply -f namespace.yaml
```

### Step 2: Create ConfigMap

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: orchestrator-config
  namespace: alert-orchestrator
data:
  orchestrator_config.yaml: |
    # Paste your orchestrator_config.yaml content here
```

```bash
kubectl apply -f configmap.yaml
```

### Step 3: Create Secret

```bash
kubectl create secret generic orchestrator-auth \
  --from-literal=username=admin \
  --from-literal=password=secure_password \
  -n alert-orchestrator
```

### Step 4: Create Deployment

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: alert-orchestrator
  namespace: alert-orchestrator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: alert-orchestrator
  template:
    metadata:
      labels:
        app: alert-orchestrator
    spec:
      containers:
      - name: orchestrator
        image: obenomar/alert-orchestrator:latest
        ports:
        - containerPort: 9808
          name: metrics
        env:
        - name: TZ
          value: "Asia/Tokyo"
        - name: LOG_LEVEL
          value: "INFO"
        - name: CONFIG_PATH
          value: "/app/config/orchestrator_config.yaml"
        - name: WO_BASIC_AUTH_USER
          valueFrom:
            secretKeyRef:
              name: orchestrator-auth
              key: username
        - name: WO_BASIC_AUTH_PASS
          valueFrom:
            secretKeyRef:
              name: orchestrator-auth
              key: password
        volumeMounts:
        - name: config
          mountPath: /app/config
          readOnly: true
        - name: output
          mountPath: /app/output
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /metrics
            port: 9808
          initialDelaySeconds: 30
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /metrics
            port: 9808
          initialDelaySeconds: 10
          periodSeconds: 10
      volumes:
      - name: config
        configMap:
          name: orchestrator-config
      - name: output
        emptyDir: {}
```

```bash
kubectl apply -f deployment.yaml
```

### Step 5: Create Service

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: alert-orchestrator
  namespace: alert-orchestrator
spec:
  selector:
    app: alert-orchestrator
  ports:
  - port: 9808
    targetPort: 9808
    name: metrics
  type: ClusterIP
```

```bash
kubectl apply -f service.yaml
```

### Step 6: Create Ingress (Optional)

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: alert-orchestrator
  namespace: alert-orchestrator
  annotations:
    nginx.ingress.kubernetes.io/auth-type: basic
    nginx.ingress.kubernetes.io/auth-secret: orchestrator-auth
spec:
  rules:
  - host: orchestrator.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: alert-orchestrator
            port:
              number: 9808
```

```bash
kubectl apply -f ingress.yaml
```

### Step 7: Verify Deployment

```bash
# Check pod status
kubectl get pods -n alert-orchestrator

# Check logs
kubectl logs -f deployment/alert-orchestrator -n alert-orchestrator

# Port forward for testing
kubectl port-forward svc/alert-orchestrator 9808:9808 -n alert-orchestrator

# Test locally
curl http://localhost:9808/metrics
```

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONFIG_PATH` | Yes | `/app/config/orchestrator_config.yaml` | Path to config file |
| `TZ` | No | `UTC` | Timezone |
| `LOG_LEVEL` | No | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `WO_BASIC_AUTH_USER` | Yes* | - | Username for protected endpoints |
| `WO_BASIC_AUTH_PASS` | Yes* | - | Password for protected endpoints |

*Required if `auth.enabled: true` in config

### Volume Mounts

| Path | Type | Purpose |
|------|------|---------|
| `/app/config` | Read-only | Configuration files |
| `/app/output` | Read-write | Diagnostic plots, logs |

### Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 9808 | HTTP | Metrics endpoint, dashboard |

### Resource Requirements

**Minimum**:
- CPU: 0.25 cores
- Memory: 256 MB
- Disk: 200 MB

**Recommended**:
- CPU: 1 core
- Memory: 512 MB
- Disk: 1 GB

---

## Troubleshooting

### NumPy Import Errors

**Symptom**:
```
ImportError: libopenblas.so.0: cannot open shared object file
```

**Solution 1**: Use pre-built image (already has runtime libraries)
```bash
docker pull obenomar/alert-orchestrator:latest
```

**Solution 2**: Build on target device
```bash
sudo bash build-synology.sh
```

---

### Permission Denied Errors

**Symptom**:
```
PermissionError: [Errno 13] Permission denied: '/app/config/orchestrator_config.yaml'
```

**Solution**: Fix file ownership
```bash
# Check current ownership
ls -la /volume2/docker-vol2/liqwid-alertmanager/config/

# Fix ownership (use monitoring user UID)
sudo chown -R 1033:100 /volume2/docker-vol2/liqwid-alertmanager/
sudo chmod -R 755 /volume2/docker-vol2/liqwid-alertmanager/
```

---

### Container Exits Immediately

**Symptom**:
```
Container exits with code 1
```

**Diagnosis**:
```bash
# Check logs
docker logs alert-orchestrator

# Common causes:
# 1. Config file not found
# 2. NumPy import error
# 3. Permission denied
# 4. Invalid config syntax
```

**Solution**: See specific error message in logs

---

### Authentication Not Working

**Symptom**:
Dashboard returns 401 Unauthorized

**Solution 1**: Verify environment variables are set
```bash
docker exec alert-orchestrator env | grep WO_BASIC_AUTH
```

**Solution 2**: Check config file
```yaml
runtime:
  auth:
    enabled: true
```

**Solution 3**: Clear browser cache or use incognito mode

---

### Build Warnings

**Warning**: `One or more build-args were not consumed`

**Cause**: Build arguments not used in Dockerfile

**Solution**: Ignore if build completes successfully, or remove unused args from build script

---

### Synology Docker Requires Sudo

**Symptom**:
```
permission denied while trying to connect to Docker daemon
```

**Solution**: Use `sudo` for all Docker commands on Synology
```bash
sudo docker-compose up -d
sudo docker logs alert-orchestrator
sudo docker ps
```

---

## Best Practices

### Security

1. **Change default credentials** immediately
2. **Use read-only mounts** for config files
3. **Run as non-root user** (container does this automatically)
4. **Restrict port 9808** to local network via firewall
5. **Use HTTPS** with reverse proxy in production

### Performance

1. **Set resource limits** to prevent memory leaks
2. **Use SSD storage** for output directory
3. **Enable log rotation** (already configured)
4. **Monitor metrics** with Prometheus

### Maintenance

1. **Regular updates**: `docker pull obenomar/alert-orchestrator:latest`
2. **Backup config files** before updates
3. **Test in staging** before production updates
4. **Monitor logs** for errors
5. **Check disk space** regularly

### Monitoring

1. **Prometheus scraping**: Point to `:9808/metrics`
2. **Health checks**: Configured automatically
3. **Log aggregation**: Use Docker logging drivers
4. **Alerting**: Set up alerts on metric anomalies

---

## Additional Resources

- [DOCKER_DEPLOYMENT.md](./DOCKER_DEPLOYMENT.md) - Original Docker guide
- [SYNOLOGY_ADMIN_WORKFLOW.md](../SYNOLOGY_ADMIN_WORKFLOW.md) - Synology-specific guide
- [ARCHITECTURE.md](./ARCHITECTURE.md) - System architecture
- [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) - General troubleshooting

---

## Support

For issues specific to Docker deployment:

1. Check logs: `docker logs alert-orchestrator`
2. Verify config: `docker exec alert-orchestrator cat /app/config/orchestrator_config.yaml`
3. Test connectivity: `docker exec alert-orchestrator curl http://localhost:9808/metrics`
4. Check permissions: `ls -la /volume2/docker-vol2/liqwid-alertmanager/config/`

For application issues, see [TROUBLESHOOTING.md](./TROUBLESHOOTING.md).
