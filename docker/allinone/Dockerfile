# App image: spice_facility on top of the reusable base (docker/base/). The base already holds bench,
# Frappe, ERPNext, Frappe Health and frappe_theme with assets built, so this adds only a few small layers:
# a push that changes only spice_facility rebuilds in minutes and EC2 pulls megabytes, not gigabytes.
# Built, tested and pushed by .github/workflows/docker-publish.yml, which passes BASE_IMAGE.
ARG BASE_IMAGE=ghcr.io/medtronic-labs/spice_next_facility-base:local
FROM ${BASE_IMAGE}

USER frappe
WORKDIR /home/frappe/frappe-bench

COPY --chown=frappe:frappe apps/spice_facility apps/spice_facility

# --no-deps: spice_facility adds no Python dependencies beyond what the base installed.
RUN ./env/bin/pip install --no-cache-dir --no-deps -e ./apps/spice_facility \
	&& printf 'frappe\nerpnext\nhealthcare\nfrappe_theme\nspice_facility\n' > sites/apps.txt \
	&& bench build --app spice_facility

# sites/ is a volume at runtime and shadows what was baked there: keep the generated files outside it so
# the entrypoint can put them back on every boot.
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
	&& chown frappe:frappe /home/frappe/redis.conf /home/frappe/frappe-bench/wsgi.py \
	&& chown -R frappe:frappe /home/frappe/sites-seed

EXPOSE 8000 9000

VOLUME ["/home/frappe/frappe-bench/sites"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
