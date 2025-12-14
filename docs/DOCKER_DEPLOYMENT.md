# Docker Deployment Guide

This guide covers deploying the Alert Orchestrator using Docker on Synology Container Manager.

## Platform Support

The Docker image is built for multiple architectures automatically using Docker Buildx:
- **linux/amd64** - x86_64 Synology NAS (most models)
- **linux/arm64** - ARM64 Synology NAS

Docker will automatically use the correct image for your NAS architecture.

## Quick Start

### 1. Build the Docker Image

```bash
# From the alert_orchestrator directory
./build-docker.sh

# Or build and push to registry
./build-docker.sh --push --tag latest
```

### 2. Run with Docker Compose (Recommended)

```bash
docker-compose up -d
```

### 3. Run with Docker CLI

```bash
docker run -d \
  --name alert-orchestrator \
  --restart unless-stopped \
  -p 9808:9808 \
  -v $(pwd)/config/orchestrator_config.yaml:/app/config/orchestrator_config.yaml:ro \
  -v $(pwd)/config/token_registry.csv:/app/config/token_registry.csv:ro \
  -v $(pwd)/output:/app/output:rw \
  -e TZ=Asia/Tokyo \
  -e LOG_LEVEL=INFO \
  alert-orchestrator:latest
```

## Synology Container Manager Deployment

### Method 1: Using Docker Compose (Easiest)

1. **Open Synology Container Manager**
   - Go to `Container Manager` > `Project`
   - Click `Create`

2. **Configure Project**
   - **Project Name**: `alert-orchestrator`
   - **Path**: Select a folder on your NAS (e.g., `/docker/alert-orchestrator`)
   - **Source**: Upload the `docker-compose.yaml` file

3. **Set Up Configuration Files**
   - Place `orchestrator_config.yaml` and `token_registry.csv` in the project folder under `config/`
   - Ensure paths match the volume mounts in `docker-compose.yaml`

4. **Build and Start**
   - Click `Build` to build the image (will automatically use correct architecture)
   - Click `Start` to run the container

> **Note**: Synology Container Manager automatically builds for the correct platform (x86_64 or ARM) based on your NAS CPU architecture.

### Method 2: Using Container Manager UI

1. **Build and Push Image** (on your development machine):
   ```bash
   ./build-docker.sh --push --tag latest
   ```

2. **Pull Image on Synology**:
   - Go to `Container Manager` > `Image`
   - Click `Add` > `Add from Registry`
   - Search for and pull `alert-orchestrator:latest`

3. **Create Container in UI**:
   - Go to `Container Manager` > `Container`
   - Click `Create` > `Create from image`
   - Select `alert-orchestrator:latest`

3. **Configure Container Settings**
   - **Container Name**: `alert-orchestrator`
   - **Port Settings**:
     - Local Port: `9808` → Container Port: `9808`
   - **Volume Settings**:
     - `/docker/alert-orchestrator/config/orchestrator_config.yaml` → `/app/config/orchestrator_config.yaml` (read-only)
     - `/docker/alert-orchestrator/config/token_registry.csv` → `/app/config/token_registry.csv` (read-only)
     - `/docker/alert-orchestrator/output` → `/app/output` (read-write)
   - **Environment Variables**:
     - `TZ=Asia/Tokyo`
     - `LOG_LEVEL=INFO`
   - **Resource Limits** (optional):
     - CPU: 1 core
     - Memory: 512 MB

4. **Enable Auto-restart**
   - Check "Enable auto-restart"

5. **Apply and Start**

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_PATH` | `/app/config/orchestrator_config.yaml` | Path to config file |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `TZ` | `Asia/Tokyo` | Container timezone |

### Volume Mounts

| Host Path | Container Path | Mode | Purpose |
|-----------|----------------|------|---------|
| `./config/orchestrator_config.yaml` | `/app/config/orchestrator_config.yaml` | ro | Main configuration |
| `./config/token_registry.csv` | `/app/config/token_registry.csv` | ro | Token mappings |
| `./output` | `/app/output` | rw | Diagnostic plots and logs |

### Port Mapping

| Host Port | Container Port | Protocol | Purpose |
|-----------|----------------|----------|---------|
| 9808 | 9808 | TCP | Prometheus metrics endpoint |

## Monitoring and Management

### View Logs

```bash
# Docker Compose
docker-compose logs -f

# Docker CLI
docker logs -f alert-orchestrator
```

### Check Health Status

```bash
docker ps --filter name=alert-orchestrator --format "table {{.Names}}\t{{.Status}}"
```

### Access Metrics

Visit `http://<synology-ip>:9808/metrics` in your browser to view Prometheus metrics.

### Restart Container

```bash
# Docker Compose
docker-compose restart

# Docker CLI
docker restart alert-orchestrator
```

### Stop Container

```bash
# Docker Compose
docker-compose down

# Docker CLI
docker stop alert-orchestrator
```

## Troubleshooting

### Docker Buildx Not Available

If you see "Docker Buildx not available" when building:

**Install Docker Desktop** (includes Buildx):
- Download from https://www.docker.com/products/docker-desktop

### Container Won't Start

1. **Check logs**:
   ```bash
   docker logs alert-orchestrator
   ```

2. **Verify configuration file**:
   ```bash
   docker exec alert-orchestrator cat /app/config/orchestrator_config.yaml
   ```

3. **Check permissions**:
   - Ensure config files are readable
   - Ensure output directory is writable

### Metrics Endpoint Not Accessible

1. **Verify port mapping**:
   ```bash
   docker port alert-orchestrator
   ```

2. **Check if service is listening**:
   ```bash
   docker exec alert-orchestrator netstat -tlnp | grep 9808
   ```

3. **Test from inside container**:
   ```bash
   docker exec alert-orchestrator python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:9808/metrics').read())"
   ```

### High Memory/CPU Usage

1. **Check resource usage**:
   ```bash
   docker stats alert-orchestrator
   ```

2. **Adjust limits in `docker-compose.yaml`**:
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2.0'      # Increase if needed
         memory: 1024M    # Increase if needed
   ```

### Database Connection Issues

1. **Verify GreptimeDB is accessible from container**:
   ```bash
   docker exec alert-orchestrator ping <greptime-host>
   ```

2. **Check network configuration in `orchestrator_config.yaml`**:
   - Ensure `data.databases.greptime.host` is correct
   - Use IP address instead of hostname if DNS resolution fails

## Updating the Container

### Rebuild and Restart

```bash
# Stop current container
docker-compose down

# Pull latest code changes
git pull

# Rebuild image
docker-compose build

# Start with new image
docker-compose up -d
```

### Manual Update

```bash
# Stop and remove old container
docker stop alert-orchestrator
docker rm alert-orchestrator

# Rebuild image
docker build -t alert-orchestrator:latest .

# Start new container (use same run command as before)
```

## Security Considerations

1. **Non-root User**: Container runs as user `orchestrator` (UID 1000)
2. **Read-only Config**: Configuration files are mounted read-only
3. **Network Isolation**: Uses dedicated bridge network
4. **Resource Limits**: CPU and memory limits prevent resource exhaustion
5. **Minimal Image**: Multi-stage build reduces attack surface

## Integration with Prometheus

Add this scrape config to your Prometheus configuration:

```yaml
scrape_configs:
  - job_name: 'alert-orchestrator'
    static_configs:
      - targets: ['<synology-ip>:9808']
    scrape_interval: 60s
```

## Backup and Recovery

### Backup Configuration

```bash
# Backup config directory
tar -czf orchestrator-config-backup-$(date +%Y%m%d).tar.gz config/
```

### Backup Output/Logs

```bash
# Backup output directory
tar -czf orchestrator-output-backup-$(date +%Y%m%d).tar.gz output/
```

### Recovery

```bash
# Extract backup
tar -xzf orchestrator-config-backup-YYYYMMDD.tar.gz

# Restart container with restored config
docker-compose restart
```

## Advanced Usage

### Run One-time Evaluation

```bash
docker run --rm \
  -v $(pwd)/config:/app/config:ro \
  alert-orchestrator:latest \
  python -m src.main --config /app/config/orchestrator_config.yaml --once
```

### Debug Mode

```bash
docker run --rm -it \
  -v $(pwd)/config:/app/config:ro \
  -e LOG_LEVEL=DEBUG \
  alert-orchestrator:latest \
  python -m src.main --config /app/config/orchestrator_config.yaml --log-level DEBUG
```

### Print Normalized Config

```bash
docker run --rm \
  -v $(pwd)/config:/app/config:ro \
  alert-orchestrator:latest \
  python -m src.main --config /app/config/orchestrator_config.yaml --print-config-normalized
```

## Resource Requirements

### Minimum Requirements
- **CPU**: 0.25 cores
- **Memory**: 256 MB
- **Disk**: 100 MB (image + output)

### Recommended Requirements
- **CPU**: 1 core
- **Memory**: 512 MB
- **Disk**: 500 MB (image + output + logs)

## Support

For issues and questions:
1. Check logs: `docker logs alert-orchestrator`
2. Review configuration: `orchestrator_config.yaml`
3. Consult TROUBLESHOOTING.md
4. Check ARCHITECTURE.md for system design details
