#!/bin/bash
# ================================================
# TweetVeet — AWS EC2 Deployment Script
# ================================================
# Usage: ./scripts/deploy.sh [--setup|--deploy|--update]
#
# Prerequisites:
#   - AWS EC2 instance (Ubuntu 22.04+)
#   - SSH access configured
#   - Docker + Docker Compose installed on EC2
# ================================================

set -euo pipefail

# --- Configuration ---
EC2_HOST="${EC2_HOST:-ubuntu@your-ec2-ip}"
APP_DIR="/opt/tweetveet"
DOCKER_COMPOSE="docker compose"

print_step() {
    echo ""
    echo "=========================================="
    echo "  $1"
    echo "=========================================="
}

# --- First-time setup ---
setup() {
    print_step "Setting up EC2 instance"

    ssh "$EC2_HOST" << 'EOF'
        # Update system
        sudo apt-get update && sudo apt-get upgrade -y

        # Install Docker
        if ! command -v docker &> /dev/null; then
            curl -fsSL https://get.docker.com | sh
            sudo usermod -aG docker $USER
            echo "Docker installed — please log out and back in, then re-run."
            exit 0
        fi

        # Install Docker Compose plugin
        if ! docker compose version &> /dev/null; then
            sudo apt-get install -y docker-compose-plugin
        fi

        # Create app directory
        sudo mkdir -p /opt/tweetveet
        sudo chown $USER:$USER /opt/tweetveet

        # Create systemd service
        sudo tee /etc/systemd/system/tweetveet.service > /dev/null << 'SERVICE'
[Unit]
Description=TweetVeet Cricket Bot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/tweetveet
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
SERVICE

        sudo systemctl daemon-reload
        sudo systemctl enable tweetveet

        echo "Setup complete!"
EOF
}

# --- Deploy application ---
deploy() {
    print_step "Deploying TweetVeet to EC2"

    # Copy project files
    rsync -avz --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='.env' \
        --exclude='*.pyc' \
        ./ "$EC2_HOST:$APP_DIR/"

    # Copy .env file
    if [ -f .env ]; then
        scp .env "$EC2_HOST:$APP_DIR/.env"
    else
        echo "WARNING: No .env file found. Copy .env.example and configure it."
    fi

    # Build and start
    ssh "$EC2_HOST" << EOF
        cd $APP_DIR
        $DOCKER_COMPOSE build --no-cache
        $DOCKER_COMPOSE down || true
        $DOCKER_COMPOSE up -d
        echo ""
        echo "Deployment complete!"
        echo "API: http://\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8000"
        echo "Health: http://\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8000/health"
        echo "Docs: http://\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8000/docs"
EOF
}

# --- Update (rebuild + restart) ---
update() {
    print_step "Updating TweetVeet on EC2"

    rsync -avz --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='.env' \
        --exclude='*.pyc' \
        ./ "$EC2_HOST:$APP_DIR/"

    ssh "$EC2_HOST" << EOF
        cd $APP_DIR
        $DOCKER_COMPOSE build
        $DOCKER_COMPOSE up -d
        echo "Update complete!"
EOF
}

# --- Main ---
case "${1:-deploy}" in
    --setup)  setup  ;;
    --deploy) deploy ;;
    --update) update ;;
    *)
        echo "Usage: $0 [--setup|--deploy|--update]"
        echo "  --setup   First-time EC2 setup (Docker, systemd)"
        echo "  --deploy  Full deploy (build + start)"
        echo "  --update  Quick update (rebuild + restart)"
        exit 1
        ;;
esac
