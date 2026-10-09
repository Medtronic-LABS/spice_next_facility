# All-in-one production image for spice_next_facility: bench (Frappe + ERPNext + Frappe Health +
# frappe_theme + spice_facility) + Redis, all run as supervised processes in one container. MariaDB runs
# beside it in its own container (deploy/docker-compose.yml). Built, tested and pushed to GHCR by
# .github/workflows/docker-publish.yml; modelled on frappe_uhis's docker/allinone image.
#
# The app checkouts under apps/ are made by the workflow at pinned refs (no `bench get-app` at build
# time): what CI tested is exactly what ships.
FROM frappe/bench:latest

USER root

ARG FRAPPE_BRANCH=version-16
ENV PATH="/home/frappe/.local/bin:${PATH}"

# mariadb-client: wait for the DB and `bench backup`; awscli: off-box backups to S3.
RUN apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
		mariadb-client \
		redis-server \
		supervisor \
		awscli \
		curl \
	&& rm -rf /var/lib/apt/lists/* \
	&& mkdir -p /var/log/supervisor \
	&& chown -R frappe:frappe /var/log/supervisor

USER frappe
WORKDIR /home/frappe

RUN command -v bench >/dev/null 2>&1 || pip3 install --user --break-system-packages frappe-bench

RUN bench init --frappe-branch "${FRAPPE_BRANCH}" --python python3 \
	--skip-redis-config-generation --no-backups frappe-bench

WORKDIR /home/frappe/frappe-bench

# Dependency order: erpnext -> healthcare (requires erpnext) -> frappe_theme -> spice_facility
# (requires all three).
COPY --chown=frappe:frappe apps/erpnext        apps/erpnext
COPY --chown=frappe:frappe apps/healthcare     apps/healthcare
COPY --chown=frappe:frappe apps/frappe_theme   apps/frappe_theme
COPY --chown=frappe:frappe apps/spice_facility apps/spice_facility

# COPYing source skips the `yarn install` that `bench get-app` would run; do it for every app that has
# frontend dependencies, or `bench build` cannot resolve their imports.
RUN for app in erpnext healthcare frappe_theme spice_facility; do \
		if [ -f "apps/${app}/package.json" ]; then yarn --cwd "apps/${app}" install --check-files; fi; \
	done

# sites/apps.txt is rewritten whole: `bench init` leaves it without a trailing newline, so appending
# would glue two app names together.
RUN ./env/bin/pip install --no-cache-dir \
		-e ./apps/erpnext \
		-e ./apps/healthcare \
		-e ./apps/frappe_theme \
		-e ./apps/spice_facility \
	&& printf 'frappe\nerpnext\nhealthcare\nfrappe_theme\nspice_facility\n' > sites/apps.txt

# Build-time stub so `bench build` runs without a database; entrypoint.sh rewrites it on every boot.
RUN cat > sites/common_site_config.json <<'JSON'
{
  "redis_cache": "redis://127.0.0.1:6379/0",
  "redis_queue": "redis://127.0.0.1:6379/1",
  "redis_socketio": "redis://127.0.0.1:6379/2",
  "socketio_port": 9000
}
JSON

RUN bench build

# sites/ is a volume at runtime and shadows what was baked there: keep generated files outside it so
# entrypoint.sh can put them back on every boot.
RUN mkdir -p /home/frappe/sites-seed \
	&& cp sites/apps.txt  /home/frappe/sites-seed/apps.txt \
	&& cp sites/apps.json /home/frappe/sites-seed/apps.json \
	&& cp -r sites/assets /home/frappe/sites-seed/assets

USER root
COPY apps/spice_facility/docker/allinone/supervisord.conf /etc/supervisor/supervisord.conf
COPY apps/spice_facility/docker/allinone/entrypoint.sh    /usr/local/bin/entrypoint.sh
COPY apps/spice_facility/docker/allinone/site-setup.sh    /usr/local/bin/site-setup.sh
COPY apps/spice_facility/docker/allinone/wait-for-site.sh /usr/local/bin/wait-for-site.sh
COPY apps/spice_facility/docker/allinone/run-backend.sh   /usr/local/bin/run-backend.sh
COPY apps/spice_facility/docker/allinone/backup.sh        /usr/local/bin/backup.sh
COPY apps/spice_facility/docker/allinone/redis.conf       /home/frappe/redis.conf
COPY apps/spice_facility/docker/allinone/wsgi.py          /home/frappe/frappe-bench/wsgi.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/site-setup.sh /usr/local/bin/wait-for-site.sh \
		/usr/local/bin/run-backend.sh /usr/local/bin/backup.sh \
	&& mkdir -p /home/frappe/.redis \
	&& chown -R frappe:frappe /home/frappe/frappe-bench /home/frappe/sites-seed /home/frappe/.redis \
		/home/frappe/redis.conf

EXPOSE 8000 9000

VOLUME ["/home/frappe/frappe-bench/sites"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
