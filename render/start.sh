#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="/tmp/itopscc-logs"
mkdir -p "$LOG_DIR"

AUTH_PORT=8001
ORDERS_PORT=8002
PAYMENTS_PORT=8003
DB_PROXY_PORT=8004
CENTRAL_PORT=8000
PROMETHEUS_PORT=9090
FRONTEND_PORT="${PORT:-10000}"

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://postgres:postgres@localhost:5432/itopscc}"

case "$DATABASE_URL" in
  postgresql+psycopg2://*) ;;
  postgres://*)
    DATABASE_URL="postgresql+psycopg2://${DATABASE_URL#postgres://}"
    ;;
  postgresql://*)
    DATABASE_URL="postgresql+psycopg2://${DATABASE_URL#postgresql://}"
    ;;
esac

export DATABASE_URL

export AUTH_SERVICE_URL="${AUTH_SERVICE_URL:-http://127.0.0.1:${AUTH_PORT}}"
export ORDERS_SERVICE_URL="${ORDERS_SERVICE_URL:-http://127.0.0.1:${ORDERS_PORT}}"
export PAYMENTS_SERVICE_URL="${PAYMENTS_SERVICE_URL:-http://127.0.0.1:${PAYMENTS_PORT}}"
export DB_PROXY_SERVICE_URL="${DB_PROXY_SERVICE_URL:-http://127.0.0.1:${DB_PROXY_PORT}}"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:${PROMETHEUS_PORT}}"

export ERROR_RATE_THRESHOLD="${ERROR_RATE_THRESHOLD:-0.10}"
export LATENCY_P95_THRESHOLD_SECONDS="${LATENCY_P95_THRESHOLD_SECONDS:-1.0}"
export AVAILABILITY_THRESHOLD="${AVAILABILITY_THRESHOLD:-0.99}"
export POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-15}"

export GEMINI_API_KEY="${GEMINI_API_KEY:-}"
export GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.6-flash}"
export LLM_ENABLED="${LLM_ENABLED:-true}"
export CORS_ORIGINS="${CORS_ORIGINS:-*}"

export JWT_SECRET_KEY="${JWT_SECRET_KEY:-dev-only-jwt-secret-change-me}"
export JWT_EXPIRE_MINUTES="${JWT_EXPIRE_MINUTES:-480}"

export SEED_ADMIN_PASSWORD="${SEED_ADMIN_PASSWORD:-changeme-admin}"
export SEED_IT_SUPPORT_PASSWORD="${SEED_IT_SUPPORT_PASSWORD:-changeme-itsupport}"
export SEED_INCIDENT_MANAGER_PASSWORD="${SEED_INCIDENT_MANAGER_PASSWORD:-changeme-incidentmgr}"
export SEED_RCA_REVIEWER_PASSWORD="${SEED_RCA_REVIEWER_PASSWORD:-changeme-rcareviewer}"

export FAULT_INJECTION_TOKEN="${FAULT_INJECTION_TOKEN:-dev-admin-token-change-me}"

PIDS=()

cleanup() {
    echo "Stopping IT Operations Command Center..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

start_fastapi() {
    local name="$1"
    local directory="$2"
    local port="$3"

    echo "Starting $name on port $port..."

    python3 -m uvicorn app.main:app \
        --app-dir "$PROJECT_ROOT/$directory" \
        --host 127.0.0.1 \
        --port "$port" \
        >>"$LOG_DIR/$name.log" 2>&1 &

    PIDS+=("$!")
}

echo "Starting backend services..."

start_fastapi "auth-service" \
    "services/auth-service" \
    "$AUTH_PORT"

start_fastapi "orders-service" \
    "services/orders-service" \
    "$ORDERS_PORT"

start_fastapi "payments-service" \
    "services/payments-service" \
    "$PAYMENTS_PORT"

start_fastapi "db-proxy-service" \
    "services/db-proxy-service" \
    "$DB_PROXY_PORT"

echo "Waiting for backend services..."
sleep 5

echo "Starting central-platform..."

start_fastapi "central-platform" \
    "central-platform" \
    "$CENTRAL_PORT"

echo "Waiting for central-platform..."
sleep 3

echo "Starting Prometheus..."

prometheus \
    --config.file="$PROJECT_ROOT/render/prometheus.yml" \
    --storage.tsdb.path=/tmp/prometheus \
    --web.listen-address="127.0.0.1:${PROMETHEUS_PORT}" \
    >>"$LOG_DIR/prometheus.log" 2>&1 &

PIDS+=("$!")

echo "Creating Render Nginx configuration..."

cat > /tmp/nginx-render.conf <<EOF
worker_processes 1;

events {
    worker_connections 512;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    access_log /tmp/nginx-access.log;
    error_log /tmp/nginx-error.log;

    server {
        listen ${FRONTEND_PORT};

        root ${PROJECT_ROOT}/frontend;
        index index.html;

        location /api/ {
            proxy_pass http://127.0.0.1:8000/;
            proxy_http_version 1.1;

            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }

        location / {
            try_files \$uri \$uri/ /index.html;
        }
    }
}
EOF

echo "Starting Nginx on public port ${FRONTEND_PORT}..."

echo "=================================================="
echo "IT Operations Command Center started"
echo "Frontend:     port ${FRONTEND_PORT}"
echo "Central API:  port ${CENTRAL_PORT}"
echo "Prometheus:   port ${PROMETHEUS_PORT}"
echo "=================================================="

exec nginx \
    -c /tmp/nginx-render.conf \
    -g "daemon off;"