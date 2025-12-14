#!/bin/bash
# Build script for building directly on Synology NAS
# This ensures NumPy is compiled for the exact target architecture
# Usage: 
#   1. Copy entire alert directory to Synology
#   2. SSH into Synology and navigate to the directory
#   3. Run: bash build-synology.sh

set -e

echo "=========================================="
echo "Building Alert Orchestrator on Synology"
echo "=========================================="
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker not found. Please install Docker on Synology."
    exit 1
fi

# Get current directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Detect UID/GID for the monitoring user (or fallback to current user)
# On Synology, specify the user that owns the config files
echo "Detecting user permissions..."
if id monitoring &>/dev/null; then
    USER_UID=$(id -u monitoring)
    USER_GID=$(id -g monitoring)
    echo "✓ Using monitoring user: UID=${USER_UID}, GID=${USER_GID}"
    echo "  (This user should own /volume2/docker-vol2/liqwid-alertmanager/)"
else
    USER_UID=$(id -u)
    USER_GID=$(id -g)
    echo "⚠ monitoring user not found, using current user: UID=${USER_UID}, GID=${USER_GID}"
fi
echo ""

# Verify required files exist
echo "Checking required files..."
REQUIRED_FILES=(
    "Dockerfile"
    "requirements.txt"
    "src/main.py"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "❌ Error: Required file not found: $file"
        exit 1
    fi
    echo "✓ $file"
done
echo ""

# Build locally with matching UID/GID
echo "Building image for local architecture (x86_64)..."
echo "⏱ This will take 5-10 minutes as NumPy is compiled from source..."
echo ""

docker build \
    --tag obenomar/alert-orchestrator:local \
    --tag obenomar/alert-orchestrator:latest \
    --build-arg USER_UID=${USER_UID} \
    --build-arg USER_GID=${USER_GID} \
    .

echo ""
echo "✅ Build completed successfully"
echo ""
echo "=========================================="
echo "Image Information:"
echo "=========================================="
docker images obenomar/altert-orchestrator:local
echo ""

echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo ""
echo "1️⃣ Ensure config directory has correct permissions:"
echo "   sudo chown -R ${USER_UID}:${USER_GID} /volume2/docker-vol2/liqwid-alertmanager/"
echo "   sudo chmod -R 755 /volume2/docker-vol2/liqwid-alertmanager/"
echo ""
echo "2️⃣ Deploy with docker-compose (use sudo on Synology):"
echo "   sudo docker-compose up -d"
echo ""
echo "3️⃣ Check logs:"
echo "   sudo docker logs -f alert-orchestrator"
echo ""
echo "Note: Synology requires 'sudo' for all Docker commands."
echo ""
echo "Build complete! 🎉"
