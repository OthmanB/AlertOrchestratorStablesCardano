# Alert Orchestrator - Production Docker Image
# Multi-platform support for Synology NAS (x86_64 and ARM64)
# Uses Docker Buildx for cross-platform compilation

# ============================================================================
# Stage 1: Builder - Compile dependencies for target architecture
# ============================================================================
FROM python:3.11-slim as builder

# Suppress debconf warnings in non-interactive build
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies for compiling NumPy and other C extensions
# Added openblas for NumPy numerical operations support
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies
# Force NumPy compilation from source to ensure compatibility with target architecture
COPY requirements.txt .
#RUN pip install --no-cache-dir --upgrade pip && \
#    pip install --no-cache-dir --no-binary numpy -r requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ============================================================================
# Stage 2: Runtime - Minimal production image
# ============================================================================
FROM python:3.11-slim

# Labels for container identification
LABEL maintainer="alert-orchestrator"
LABEL description="Alert System - Monitors Liqwid positions and provides withdrawal recommendations"
LABEL version="1.0.0"

# Suppress debconf warnings in non-interactive build
ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies (curl for health check)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libopenblas0 \
    liblapack3 \
    libgfortran5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder (includes compiled NumPy for target arch)
COPY --from=builder /opt/venv /opt/venv

# Set working directory
WORKDIR /app

# Copy application code
COPY src/ ./src/
COPY __init__.py ./

# Create directories
RUN mkdir -p /app/config /app/output/plots

# IMPORTANT: Match Synology user permissions
# Default: monitoring user (UID=1033, GID=100)
# Override with: docker build --build-arg USER_UID=<your_uid> --build-arg USER_GID=<your_gid>
ARG USER_UID=1033
ARG USER_GID=100
ARG USER_NAME=monitoring
ARG USER_GROUP=users

# Create user with matching UID/GID
# Note: Group 100 (users) already exists in base image, so groupadd may fail - that's okay
#RUN (groupadd -g ${USER_GID} orchestrator 2>/dev/null || echo "Group ${USER_GID} already exists") && \
#    useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash orchestrator && \
#    chown -R orchestrator:orchestrator /app
# GID 100 is the 'users' group in Debian (already exists), so we use it directly
# We create the user with the specified UID and assign them to the existing GID
RUN useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash ${USER_NAME} && \
    chown -R ${USER_NAME}:${USER_GROUP} /app

# Switch to non-root user
USER ${USER_NAME}

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app:${PYTHONPATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH="/app/config/orchestrator_config.yaml"

# Expose Prometheus metrics port (default: 9808)
EXPOSE 9808

# Health check - verify metrics endpoint is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:9808/metrics || exit 1

# Default command: run orchestrator with config from environment or default
CMD ["python", "-m", "src.main", "--config", "/app/config/orchestrator_config.yaml"]