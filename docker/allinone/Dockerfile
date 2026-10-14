# App image: spice_facility on top of the reusable base (docker/base/). The base already holds bench,
# Frappe, ERPNext, Frappe Health and frappe_theme with assets built, so this adds only a few small layers:
# a push that changes only spice_facility rebuilds in minutes and EC2 pulls megabytes, not gigabytes.
# Built, tested and pushed by .github/workflows/docker-publish.yml, which passes BASE_IMAGE.
ARG BASE_IMAGE=ghcr.io/medtronic-labs/spice_next_facility-base:local
FROM ${BASE_IMAGE}

USER frappe
WORKDIR /home/frappe/frappe-bench

COPY --chown=frappe:frappe apps/spice_facility apps/spice_facility

# What can change the database: the pinned Frappe/ERPNext/Health/frappe_theme versions, and spice_facility's
# DocTypes and other JSON metadata, hooks, patches, module list and the code its after_migrate runs (setup,
# desk navigation, form layouts, and ai/config.py whose field names and settings it creates).
# site-setup.sh skips `bench migrate` when this matches the site's last migrate. JS/CSS, templates, tests
# and other runtime-only Python (api/, the rest of ai/) cannot change the schema.
RUN cd apps/spice_facility \
	&& { cat docker/base/versions.env; \
	     find spice_facility \( -path '*/public' -o -path '*/tests' -o -path '*/templates' -o -name __pycache__ \) -prune -o \
	          -type f \( -name '*.json' -o -name hooks.py -o -name patches.txt -o -name modules.txt \
	                     -o -path '*/patches/*.py' -o -path '*/setup/*.py' -o -path '*/desk_navigation/*.py' \
	                     -o -path '*/form_ux/*.py' -o -path 'spice_facility/ai/config.py' \) -print \
	          | LC_ALL=C sort | xargs sha256sum; } \
	| sha256sum | cut -d' ' -f1 > /home/frappe/migrate-fingerprint

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
