#!/bin/bash
# TripSync FastAPI — VPS Deployment Script
# Run once on a fresh Ubuntu 22.04 / Debian 12 VPS

set -e

APP_DIR="/opt/tripsync"
REPO_URL="your-git-repo-url"   # Replace with your actual repo URL
DOMAIN="api.yourdomain.com"    # Replace with your actual domain
PORT=4010

echo "=== TripSync FastAPI Deployment ==="

# ── System packages ────────────────────────────────────────────────────────
apt-get update && apt-get upgrade -y
apt-get install -y python3.12 python3.12-dev python3-pip python3-venv \
    postgresql postgresql-contrib redis-server nginx certbot \
    python3-certbot-nginx git build-essential libpq-dev

# ── App directory ──────────────────────────────────────────────────────────
mkdir -p "$APP_DIR"
cd "$APP_DIR"

if [ -d ".git" ]; then
    git pull
else
    git clone "$REPO_URL" .
fi

# ── Python virtualenv ──────────────────────────────────────────────────────
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ── Environment ────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ">>> EDIT $APP_DIR/.env with your credentials before continuing <<<"
    exit 1
fi

# ── PostgreSQL setup ────────────────────────────────────────────────────────
sudo -u postgres psql -c "CREATE DATABASE tripsync;" 2>/dev/null || true
sudo -u postgres psql -c "CREATE USER tripsync WITH PASSWORD 'change-me-strong-password';" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE tripsync TO tripsync;" 2>/dev/null || true

# ── Redis setup ─────────────────────────────────────────────────────────────
systemctl enable redis-server
systemctl start redis-server

# ── Database migrations ─────────────────────────────────────────────────────
source venv/bin/activate
alembic upgrade head

# ── Systemd service — API ──────────────────────────────────────────────────
cat > /etc/systemd/system/tripsync-api.service << EOF
[Unit]
Description=TripSync FastAPI
After=network.target postgresql.service redis.service

[Service]
User=www-data
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT --workers 4 --loop uvloop --http httptools
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$APP_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

# ── Systemd service — Celery Worker ───────────────────────────────────────
cat > /etc/systemd/system/tripsync-worker.service << EOF
[Unit]
Description=TripSync Celery Worker
After=network.target redis.service

[Service]
User=www-data
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/celery -A app.celery_app worker --loglevel=info --concurrency=4
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$APP_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

# ── Systemd service — Celery Beat ─────────────────────────────────────────
cat > /etc/systemd/system/tripsync-beat.service << EOF
[Unit]
Description=TripSync Celery Beat Scheduler
After=network.target redis.service

[Service]
User=www-data
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/celery -A app.celery_app beat --loglevel=info
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$APP_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

chown -R www-data:www-data "$APP_DIR"

systemctl daemon-reload
systemctl enable tripsync-api tripsync-worker tripsync-beat
systemctl start tripsync-api tripsync-worker tripsync-beat

# ── Nginx config ───────────────────────────────────────────────────────────
cat > /etc/nginx/sites-available/tripsync << EOF
upstream tripsync_api {
    server 127.0.0.1:$PORT;
    keepalive 32;
}

server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 25M;

    location /health {
        proxy_pass http://tripsync_api/health;
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

ln -sf /etc/nginx/sites-available/tripsync /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# ── SSL via certbot ────────────────────────────────────────────────────────
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@yourdomain.com

echo ""
echo "=== Deployment complete! ==="
echo "API running at https://$DOMAIN"
echo "Docs at https://$DOMAIN/docs  (only in non-production)"
echo ""
echo "Useful commands:"
echo "  systemctl status tripsync-api"
echo "  journalctl -u tripsync-api -f"
echo "  systemctl restart tripsync-api"
