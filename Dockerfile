FROM python:3.12-slim

WORKDIR /app

# Install Nginx and utilities required by the deployment
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       nginx \
       wget \
       ca-certificates \
       tar \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies for Central Platform and all four services
COPY central-platform/requirements.txt /tmp/central-requirements.txt
COPY services/auth-service/requirements.txt /tmp/auth-requirements.txt
COPY services/orders-service/requirements.txt /tmp/orders-requirements.txt
COPY services/payments-service/requirements.txt /tmp/payments-requirements.txt
COPY services/db-proxy-service/requirements.txt /tmp/db-proxy-requirements.txt

RUN pip install --no-cache-dir \
    -r /tmp/central-requirements.txt \
    -r /tmp/auth-requirements.txt \
    -r /tmp/orders-requirements.txt \
    -r /tmp/payments-requirements.txt \
    -r /tmp/db-proxy-requirements.txt

# Copy the existing application code
COPY central-platform /app/central-platform
COPY services /app/services
COPY frontend /app/frontend
COPY render /app/render

# Install Prometheus
RUN wget -q \
    https://github.com/prometheus/prometheus/releases/download/v2.54.1/prometheus-2.54.1.linux-amd64.tar.gz \
    -O /tmp/prometheus.tar.gz \
    && tar -xzf /tmp/prometheus.tar.gz -C /tmp \
    && cp /tmp/prometheus-2.54.1.linux-amd64/prometheus /usr/local/bin/prometheus \
    && cp /tmp/prometheus-2.54.1.linux-amd64/promtool /usr/local/bin/promtool \
    && chmod +x /usr/local/bin/prometheus /usr/local/bin/promtool \
    && rm -rf /tmp/prometheus*

# Render exposes one public HTTP port through $PORT.
EXPOSE 10000

# Start all internal services + Prometheus + Nginx.
CMD ["bash", "/app/render/start.sh"]