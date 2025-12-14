# Docker Deployment - Quick Reference

This is a quick reference guide for Docker deployment. For the complete guide, see [docs/DOCKER_COMPLETE_GUIDE.md](docs/DOCKER_COMPLETE_GUIDE.md).

## Quick Start

### Pull and Run

```bash
docker pull obenomar/alert-orchestrator:latest

docker run -d \
  --name alert-orchestrator \
  -p 9808:9808 \
  -v /path/to/config:/app/config:ro \
  -v /path/to/output:/app/output:rw \
  -e WO_BASIC_AUTH_USER=admin \
  -e WO_BASIC_AUTH_PASS=secure_password \
  obenomar/alert-orchestrator:latest
```

### Using Docker Compose

```bash
# Edit docker-compose.yaml to set auth credentials
docker-compose up -d
```

## Build Scripts

### `build-docker.sh` - Multi-Platform Build (Mac/Linux/Windows)

**Purpose**: Build on development machine, push to Docker Hub

```bash
# Build for amd64 and arm64
./build-docker.sh --push --tag latest
```

**Features**:
- ✅ Builds for linux/amd64 and linux/arm64
- ✅ Uses Docker Buildx
- ✅ Fast (uses pre-built NumPy wheels)
- ✅ Can push to Docker Hub

**Requirements**:
- Docker Desktop or Docker with Buildx
- Docker Hub login (for --push)

---

### `build-synology.sh` - Single-Platform Build (Target Device)

**Purpose**: Build directly on Synology NAS or deployment target

```bash
# On Synology NAS (as admin)
sudo bash build-synology.sh
```

**Features**:
- ✅ Builds for local architecture
- ✅ No Buildx required
- ✅ Auto-detects user UID/GID
- ✅ 100% compatibility guarantee

**Requirements**:
- Docker installed on target
- SSH access
- Sudo privileges

---

## Deployment Scenarios

### Scenario 1: Quick Test on Mac/Linux

```bash
# Build locally
./build-docker.sh

# Run with docker-compose
docker-compose up -d
```

### Scenario 2: Production on Synology NAS

```bash
# 1. Transfer files to Synology
scp -r ./* admin@synology-ip:/volume1/docker/alert_orchestrator/

# 2. SSH and build
ssh admin@synology-ip
cd /volume1/docker/alert_orchestrator
sudo bash build-synology.sh

# 3. Deploy
sudo docker-compose up -d
```

### Scenario 3: Kubernetes Cluster

```bash
# 1. Build and push from Mac
./build-docker.sh --push --tag v1.0.0

# 2. Apply Kubernetes manifests
kubectl apply -f k8s/
```

---

## Configuration

### Required Files

| File | Location | Purpose |
|------|----------|---------|
| `orchestrator_config.yaml` | `/app/config/` | Main configuration |
| `token_registry.csv` | `/app/config/` | Token definitions |

### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WO_BASIC_AUTH_USER` | Yes* | Dashboard username |
| `WO_BASIC_AUTH_PASS` | Yes* | Dashboard password |
| `CONFIG_PATH` | No | Config file path (default: `/app/config/orchestrator_config.yaml`) |
| `TZ` | No | Timezone (default: `UTC`) |
| `LOG_LEVEL` | No | Log level (default: `INFO`) |

*Required if `auth.enabled: true` in config

### Volume Mounts

| Container Path | Type | Purpose |
|---------------|------|---------|
| `/app/config` | Read-only | Configuration files |
| `/app/output` | Read-write | Diagnostic plots, logs |

---

## Docker Compose Configuration

### Synology NAS Setup

```yaml
version: '3.8'

services:
  alert-orchestrator:
    image: obenomar/alert-orchestrator:latest
    container_name: alert-orchestrator
    restart: unless-stopped
    
    ports:
      - "9808:9808"
    
    volumes:
      # Synology config path
      - /volume2/docker-vol2/liqwid-alertmanager/config:/app/config:ro
      - ./output:/app/output:rw
    
    environment:
      - TZ=Asia/Tokyo
      - LOG_LEVEL=INFO
      - WO_BASIC_AUTH_USER=admin          # CHANGE THIS!
      - WO_BASIC_AUTH_PASS=changeme123    # CHANGE THIS!
```

**Important**: 
1. Change default credentials!
2. Ensure config directory exists and has correct permissions
3. Use `sudo` for all Docker commands on Synology

---

## Troubleshooting

### NumPy Import Error

**Symptom**: `ImportError: libopenblas.so.0: cannot open shared object file`

**Solution**: Build on target device
```bash
sudo bash build-synology.sh
```

### Permission Denied

**Symptom**: `PermissionError: [Errno 13] Permission denied`

**Solution**: Fix file ownership
```bash
sudo chown -R 1033:100 /volume2/docker-vol2/liqwid-alertmanager/
sudo chmod -R 755 /volume2/docker-vol2/liqwid-alertmanager/
```

### Container Exits Immediately

**Diagnosis**:
```bash
docker logs alert-orchestrator
```

**Common Causes**:
- Config file not found
- Invalid config syntax
- NumPy import error
- Permission denied

---

## Endpoints

| Endpoint | Authentication | Purpose |
|----------|----------------|---------|
| `http://host:9808/metrics` | None | Prometheus metrics (public) |
| `http://host:9808/dashboard` | Basic Auth | Web dashboard (private) |
| `http://host:9808/recommendations` | Basic Auth | JSON API (private) |
| `http://host:9808/health` | None | Health check (public) |

---

## Files Reference

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage Docker image definition |
| `docker-compose.yaml` | Docker Compose deployment config |
| `build-docker.sh` | Multi-platform build script (Mac/Linux) |
| `build-synology.sh` | Single-platform build script (Target device) |
| `docs/DOCKER_COMPLETE_GUIDE.md` | Complete Docker deployment guide |
| `docs/DOCKER_DEPLOYMENT.md` | Docker basics and quick start |
| `SYNOLOGY_ADMIN_WORKFLOW.md` | Synology-specific deployment guide |
| `SYNOLOGY_BUILD.md` | Synology build instructions |
| `SYNOLOGY_QUICKSTART.md` | Synology quick reference |

---

## Command Quick Reference

### Docker Commands

```bash
# Pull image
docker pull obenomar/alert-orchestrator:latest

# Run container
docker run -d --name alert-orchestrator -p 9808:9808 \
  -v ./config:/app/config:ro -v ./output:/app/output:rw \
  obenomar/alert-orchestrator:latest

# Check logs
docker logs -f alert-orchestrator

# Execute command in container
docker exec -it alert-orchestrator bash

# Stop container
docker stop alert-orchestrator

# Remove container
docker rm alert-orchestrator
```

### Docker Compose Commands

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Restart services
docker-compose restart

# View logs
docker-compose logs -f

# Check status
docker-compose ps
```

### Synology Commands (use sudo)

```bash
# Build image
sudo bash build-synology.sh

# Start container
sudo docker-compose up -d

# Check logs
sudo docker logs -f alert-orchestrator

# Check status
sudo docker ps | grep alert-orchestrator

# Execute command
sudo docker exec alert-orchestrator python -c "import numpy; print(numpy.__version__)"
```

---

## Architecture Notes

### Image Architecture

- **Base**: `python:3.11-slim` (Debian-based)
- **Platforms**: `linux/amd64`, `linux/arm64`
- **Size**: ~150 MB
- **User**: `monitoring` (UID 1033, GID 100)

### Build Process

**Stage 1: Builder**
- Installs build dependencies (gcc, g++, etc.)
- Creates virtual environment
- Installs Python packages (NumPy, etc.)

**Stage 2: Runtime**
- Minimal base image
- Copies virtual environment from builder
- Installs runtime libraries only (libopenblas, etc.)
- Creates non-root user
- Copies application code

### Why Two Build Scripts?

**`build-docker.sh`** (Multi-platform):
- Uses Docker Buildx
- Builds for multiple architectures
- Uses pre-built NumPy wheels
- Fast (3-5 minutes)
- Good for development/CI/CD

**`build-synology.sh`** (Single-platform):
- Uses standard Docker build
- Builds for local architecture only
- Compiles NumPy from source if needed
- Slower (5-10 minutes)
- 100% compatibility guarantee
- Good for production on specific hardware

---

## Additional Documentation

For more detailed information, see:

- **[docs/DOCKER_COMPLETE_GUIDE.md](docs/DOCKER_COMPLETE_GUIDE.md)** - Complete Docker deployment guide
- **[SYNOLOGY_ADMIN_WORKFLOW.md](SYNOLOGY_ADMIN_WORKFLOW.md)** - Synology admin workflow
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** - General deployment guide
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Troubleshooting guide
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System architecture

---

## Support

For Docker-specific issues:

1. Check logs: `docker logs alert-orchestrator`
2. Verify config: `docker exec alert-orchestrator cat /app/config/orchestrator_config.yaml`
3. Test NumPy: `docker exec alert-orchestrator python -c "import numpy; print(numpy.__version__)"`
4. Check permissions: `ls -la /volume2/docker-vol2/liqwid-alertmanager/config/`

For general issues, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
