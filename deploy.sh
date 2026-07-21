#!/usr/bin/env bash
# TripSync FastAPI — Vultr VPS deployment script
# Run as root on Ubuntu 24.04 / 22.04 or Debian 12.

set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/tripsync}"
REPO_DIR="${REPO_DIR:-$APP_ROOT/repo}"
BACKEND_SUBDIR="${BACKEND_SUBDIR:-fastapi_backend}"
BACKEND_DIR="${BACKEND_DIR:-$REPO_DIR/$BACKEND_SUBDIR}"
APP_USER="${APP_USER:-www-data}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GIT_BRANCH="${GIT_BRANCH:-main}"
REPO_URL="${REPO_URL:-}"
DOMAIN="${DOMAIN:-api.trawellbuddy.com}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN:-https://trawellbuddy.com}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-connect@trawellbuddy.com}"
PORT="${PORT:-4010}"
DB_NAME="${DB_NAME:-tripsync}"
DB_USER="${DB_USER:-tripsync}"
DB_PASSWORD="${DB_PASSWORD:-change-me-strong-password}"

if [[ $EUID -ne 0 ]]; then
    echo "Run this script as root."
    exit 1
fi

if [[ -z "$REPO_URL" ]]; then
    echo "Set REPO_URL before running."
    echo "Example:"
    echo "  REPO_URL=git@github.com:your-org/TripSync.git bash fastapi_backend/deploy.sh"
    exit 1
fi

echo "=== TripSync FastAPI deployment ==="
echo "DOMAIN=$DOMAIN"
echo "FRONTEND_ORIGIN=$FRONTEND_ORIGIN"
echo "REPO_DIR=$REPO_DIR"
echo "BACKEND_DIR=$BACKEND_DIR"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-dev python3-pip python3-venv \
    postgresql postgresql-contrib redis-server nginx certbot \
    python3-certbot-nginx git build-essential libpq-dev

mkdir -p "$APP_ROOT"

if [[ -d "$REPO_DIR/.git" ]]; then
    git -C "$REPO_DIR" fetch --all --prune
    git -C "$REPO_DIR" checkout "$GIT_BRANCH"
    git -C "$REPO_DIR" pull --ff-only origin "$GIT_BRANCH"
else
    rm -rf "$REPO_DIR"
    git clone --branch "$GIT_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

if [[ ! -d "$BACKEND_DIR" ]]; then
    echo "Backend directory not found: $BACKEND_DIR"
    exit 1
fi

cd "$BACKEND_DIR"

$PYTHON_BIN -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [[ ! -f ".env" ]]; then
    cp .env.production.example .env
    sed -i \
        -e "s|^FRONTEND_URL=.*|FRONTEND_URL=$FRONTEND_ORIGIN|" \
        -e "s|^ALLOWED_ORIGINS=.*|ALLOWED_ORIGINS=$FRONTEND_ORIGIN,https://www.trawellbuddy.com,https://$DOMAIN|" \
        -e "s|change-me-strong-password|$DB_PASSWORD|g" \
        .env
    echo "Created $BACKEND_DIR/.env from .env.production.example"
    echo "Fill remaining secrets, then rerun the script."
    exit 1
fi

systemctl enable postgresql redis-server nginx
systemctl restart postgresql
systemctl restart redis-server

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

source venv/bin/activate
alembic upgrade head

chown -R "$APP_USER:$APP_USER" "$APP_ROOT"

cat > /etc/systemd/system/tripsync-api.service << EOF
[Unit]
Description=TripSync FastAPI
After=network.target postgresql.service redis-server.service

[Service]
User=$APP_USER
WorkingDirectory=$BACKEND_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$BACKEND_DIR
EnvironmentFile=$BACKEND_DIR/.env
ExecStart=$BACKEND_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT --workers 4 --loop uvloop --http httptools --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/tripsync-worker.service << EOF
[Unit]
Description=TripSync Celery Worker
After=network.target redis-server.service postgresql.service

[Service]
User=$APP_USER
WorkingDirectory=$BACKEND_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$BACKEND_DIR
EnvironmentFile=$BACKEND_DIR/.env
ExecStart=$BACKEND_DIR/venv/bin/celery -A app.celery_app worker --loglevel=info --concurrency=4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/tripsync-beat.service << EOF
[Unit]
Description=TripSync Celery Beat Scheduler
After=network.target redis-server.service

[Service]
User=$APP_USER
WorkingDirectory=$BACKEND_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$BACKEND_DIR
EnvironmentFile=$BACKEND_DIR/.env
ExecStart=$BACKEND_DIR/venv/bin/celery -A app.celery_app beat --loglevel=info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/sites-available/tripsync << EOF
upstream tripsync_api {
    server 127.0.0.1:$PORT;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    client_max_body_size 25M;

    location /health {
        proxy_pass http://tripsync_api/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /socket.io/ {
        proxy_pass http://tripsync_api/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_buffering off;
    }

    location / {
        proxy_pass http://tripsync_api;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/tripsync /etc/nginx/sites-enabled/tripsync
nginx -t
systemctl restart nginx

systemctl daemon-reload
systemctl enable tripsync-api tripsync-worker tripsync-beat
systemctl restart tripsync-api tripsync-worker tripsync-beat

if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH || true
    ufw allow 'Nginx Full' || true
fi

certbot --nginx --redirect -d "$DOMAIN" --non-interactive --agree-tos -m "$LETSENCRYPT_EMAIL"

echo
echo "=== Deployment complete ==="
echo "API: https://$DOMAIN"
echo "Health: https://$DOMAIN/health"
echo
echo "Useful commands:"
echo "  systemctl status tripsync-api"
echo "  journalctl -u tripsync-api -f"
echo "  systemctl restart tripsync-api tripsync-worker tripsync-beat"
