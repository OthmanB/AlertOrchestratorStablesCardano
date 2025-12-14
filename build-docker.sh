#!/bin/bash
# Build script for Alert Orchestrator Docker image
# Uses Docker Buildx for multi-platform builds (x86_64 and ARM for Synology NAS)
# Usage: ./build-docker.sh [--push] [--tag TAG]

set -e  # Exit on error

# Default values
DOCKER_USERNAME="${DOCKER_USERNAME:-obenomar}"  # Can be overridden via environment variable
IMAGE_NAME="${DOCKER_USERNAME}/alert-orchestrator"
TAG="latest"
PUSH_IMAGE=false
# 
PLATFORMS="linux/amd64,linux/arm64"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --push)
            PUSH_IMAGE=true
            shift
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        --help)
            echo "Usage: ./build-docker.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --push          Push image to registry after build"
            echo "  --tag TAG       Set image tag (default: latest)"
            echo "  --help          Show this help message"
            echo ""
            echo "The image is built for both x86_64 and ARM64 platforms automatically."
            echo "This covers all modern Synology NAS models."
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Alert Orchestrator - Docker Build${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if Docker Buildx is available
echo -e "${BLUE}Checking Docker Buildx...${NC}"
if ! docker buildx version > /dev/null 2>&1; then
    echo -e "${RED}✗ Docker Buildx not available${NC}"
    echo -e "${YELLOW}Please install Docker Buildx or use Docker Desktop${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker Buildx available${NC}"

# Create/use buildx builder with multi-platform support
BUILDER_NAME="alert-orchestrator-builder"
if ! docker buildx inspect "$BUILDER_NAME" > /dev/null 2>&1; then
    echo -e "${BLUE}Creating buildx builder instance...${NC}"
    docker buildx create --name "$BUILDER_NAME" --use --driver docker-container
    echo -e "${GREEN}✓ Builder created: $BUILDER_NAME${NC}"
else
    echo -e "${BLUE}Using existing builder: $BUILDER_NAME${NC}"
    docker buildx use "$BUILDER_NAME"
fi
echo ""

# Verify required files exist
echo -e "${BLUE}Checking required files...${NC}"
REQUIRED_FILES=(
    "Dockerfile"
    "requirements.txt"
    "src/main.py"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo -e "${YELLOW}Warning: Required file not found: $file${NC}"
    else
        echo -e "${GREEN}✓${NC} $file"
    fi
done
echo ""

# Build the image
echo -e "${BLUE}Building Docker image for all platforms...${NC}"
echo -e "Image: ${GREEN}${IMAGE_NAME}:${TAG}${NC}"
echo -e "Platforms: ${GREEN}${PLATFORMS}${NC}"
echo ""

BUILD_ARGS=(
    --builder "$BUILDER_NAME"
    --platform "$PLATFORMS"
    --tag "${IMAGE_NAME}:${TAG}"
    --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    --build-arg VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
)

if [ "$PUSH_IMAGE" = true ]; then
    BUILD_ARGS+=(--push)
    echo -e "${BLUE}Will push to registry after build${NC}"
else
    # For local testing, load to Docker (only works when building single platform manually)
    BUILD_ARGS+=(--output type=image,push=false)
fi

BUILD_ARGS+=(.)

docker buildx build "${BUILD_ARGS[@]}"

echo ""
echo -e "${GREEN}✓ Build completed successfully${NC}"
echo ""

if [ "$PUSH_IMAGE" = true ]; then
    echo -e "${GREEN}✓ Image pushed to registry${NC}"
    echo -e "Ready to pull from your Synology NAS Container Manager"
    echo ""
fi

# Show next steps
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Next Steps:${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

if [ "$PUSH_IMAGE" = true ]; then
    echo "1. Pull image on Synology NAS Container Manager"
    echo "2. Create container with port 9808 exposed"
    echo "3. Mount volumes for config and output"
else
    echo "Image built for linux/amd64 and linux/arm64 platforms."
    echo ""
    echo "To deploy on Synology:"
    echo "1. Push to a registry:"
    echo -e "   ${GREEN}./build-docker.sh --push${NC}"
    echo ""
    echo "2. Or use docker-compose (will build automatically):"
    echo -e "   ${GREEN}docker-compose up -d${NC}"
fi

echo ""
echo -e "${BLUE}Platform Support:${NC}"
echo "  ✓ linux/amd64   - x86_64 Synology NAS (most models)"
echo "  ✓ linux/arm64   - ARM64 Synology NAS"
echo ""
echo -e "${GREEN}Build process complete!${NC}"
