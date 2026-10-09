# Base image: bench + Frappe + ERPNext + Frappe Health + frappe_theme at the release tags pinned in
# docker/base/versions.env, with assets built. Rebuilt only when versions.env or this file changes; the app
# image (docker/allinone/Dockerfile) adds spice_facility on top in a few small layers.
#
# Multi-stage: the builder does all the heavy work (yarn installs, pip, asset build); the runtime stage
# copies only the resulting bench, leaving behind the download caches (~3 GB) and the build-only
# node_modules of erpnext/healthcare/frappe_theme. Frappe's own node_modules stay: socketio.js runs on them.
ARG BENCH_IMAGE=frappe/bench:v5.31.0

FROM ${BENCH_IMAGE} AS builder
ARG FRAPPE_REF
ARG ERPNEXT_REF
ARG HEALTHCARE_REF
ARG FRAPPE_THEME_REF

USER frappe
WORKDIR /home/frappe
ENV PATH="/home/frappe/.local/bin:${PATH}"

RUN command -v bench >/dev/null 2>&1 || pip3 install --user --break-system-packages frappe-bench
RUN bench init --frappe-branch "${FRAPPE_REF}" --python python3 \
	--skip-redis-config-generation --no-backups frappe-bench

WORKDIR /home/frappe/frappe-bench

# Public repos at pinned release tags: no CI checkout or credentials needed.
RUN git clone --depth 1 --branch "${ERPNEXT_REF}" https://github.com/frappe/erpnext.git apps/erpnext \
	&& git clone --depth 1 --branch "${HEALTHCARE_REF}" https://github.com/frappe/health.git apps/healthcare \
	&& git clone --depth 1 --branch "${FRAPPE_THEME_REF}" https://github.com/Suvaidyam/frappe_theme.git apps/frappe_theme

RUN for app in erpnext healthcare frappe_theme; do \
		if [ -f "apps/${app}/package.json" ]; then yarn --cwd "apps/${app}" install --check-files; fi; \
	done

# sites/apps.txt is written whole (bench init leaves it without a trailing newline).
RUN ./env/bin/pip install --no-cache-dir -e ./apps/erpnext -e ./apps/healthcare -e ./apps/frappe_theme \
	&& printf 'frappe\nerpnext\nhealthcare\nfrappe_theme\n' > sites/apps.txt

# Build-time stub so `bench build` runs without a database; the entrypoint rewrites it on every boot.
RUN cat > sites/common_site_config.json <<'JSON'
{
  "redis_cache": "redis://127.0.0.1:6379/0",
  "redis_queue": "redis://127.0.0.1:6379/1",
  "redis_socketio": "redis://127.0.0.1:6379/2",
  "socketio_port": 9000
}
JSON

RUN bench build

# Build-only dependencies: every node_modules except frappe's (needed by socketio.js and by
# `bench build --app` in the app image).
RUN find apps -name node_modules -type d -prune ! -path "apps/frappe/node_modules" -exec rm -rf {} +


FROM ${BENCH_IMAGE}
USER root

# Runtime packages: DB client (site setup, bench backup), Redis, supervisord, AWS CLI (S3 backups).
# The MariaDB 11.8 client insists on TLS by default; the MariaDB 10.6 container offers none. The client
# config keeps TLS when the server supports it (e.g. RDS) and stops requiring it otherwise, for every
# client Frappe shells out to (mariadb, mariadb-dump, mysqladmin).
RUN apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
		mariadb-client \
		redis-server \
		supervisor \
		awscli \
		curl \
	&& rm -rf /var/lib/apt/lists/* \
	&& printf '[client]\ndisable-ssl-verify-server-cert\n' > /etc/mysql/conf.d/spice-client.cnf \
	&& mkdir -p /var/log/supervisor /home/frappe/.redis \
	&& chown -R frappe:frappe /var/log/supervisor /home/frappe/.redis

# Same base image as the builder, so the venv's interpreter path (pyenv) is identical.
COPY --from=builder --chown=frappe:frappe /home/frappe/frappe-bench /home/frappe/frappe-bench

USER frappe
WORKDIR /home/frappe/frappe-bench
ENV PATH="/home/frappe/.local/bin:${PATH}"
