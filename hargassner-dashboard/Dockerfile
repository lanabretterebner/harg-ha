FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24@sha256:51837e17a94b578cc00357c98482b61b53b27e37d910c97b86dd6448cfb11a6f

ARG BUILD_VERSION=1.5.1
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="aarch64|amd64"

COPY ingress_proxy.py app_monitor.py health_alerts.py run_app.py LICENSE /app/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["/command/with-contenv", "python3", "/app/run_app.py"]
