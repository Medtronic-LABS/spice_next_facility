#!/bin/bash
# One-shot (supervisord program `site-setup`, also run directly by CI): create the site if missing,
# install every app if missing (dependency order), migrate + clear cache, then drop the marker that
# wait-for-site.sh waits for. Safe to automate because this deployment is single-instance.
#
# Migrate is skipped when nothing that can change the database changed since the site's last migrate: the
# image's /home/frappe/migrate-fingerprint (see docker/allinone/Dockerfile) equals the copy the site keeps
# after migrating. FORCE_MIGRATE=1 (or deleting sites/<site>/.migrated-fingerprint) always migrates, e.g.
# after restoring an older backup into the site.
#
# DB_ROOT_PASSWORD is used only by `bench new-site` (to create the site's own database and scoped user);
# afterwards the site connects with the scoped credentials bench writes into site_config.json.
set -eu

: "${SITE_NAME:?SITE_NAME must be set}"
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
DB_HOST="${DB_HOST:-mariadb}"
DB_PORT="${DB_PORT:-3306}"
APPS="erpnext healthcare frappe_theme fhir_gateway spice_facility"
FINGERPRINT="/home/frappe/migrate-fingerprint"

cd /home/frappe/frappe-bench
MARKER="sites/.site_setup_complete"
rm -f "${MARKER}"

echo "[site-setup] waiting for MariaDB and Redis"
for i in $(seq 1 90); do
	mysqladmin ping -h "${DB_HOST}" -P "${DB_PORT}" -u root -p"${DB_ROOT_PASSWORD}" --silent >/dev/null 2>&1 && break
	sleep 2
done
mysqladmin ping -h "${DB_HOST}" -P "${DB_PORT}" -u root -p"${DB_ROOT_PASSWORD}" --silent >/dev/null 2>&1 || {
	echo "[site-setup] MariaDB never became reachable at ${DB_HOST}:${DB_PORT}" >&2
	exit 1
}
for i in $(seq 1 60); do
	redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1 && break
	sleep 2
done

if [ -d "sites/${SITE_NAME}" ]; then
	echo "[site-setup] site '${SITE_NAME}' already exists"
else
	echo "[site-setup] creating site '${SITE_NAME}'"
	bench new-site "${SITE_NAME}" \
		--db-host "${DB_HOST}" \
		--db-port "${DB_PORT}" \
		--db-root-username root \
		--db-root-password "${DB_ROOT_PASSWORD}" \
		--mariadb-user-host-login-scope='%' \
		--admin-password "${ADMIN_PASSWORD}"
fi

# Frappe creates the site's encryption key lazily, on the first encrypted value. Web and worker processes
# each cache site_config for up to a minute, so two of them can create different keys and a value saved
# through one (e.g. an AI provider's API key) cannot be decrypted by the other. Create it here, before any
# of them start; an existing key is never replaced (it decrypts everything already stored).
SITE_CONFIG="sites/${SITE_NAME}/site_config.json"
if ! ./env/bin/python -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1])).get("encryption_key") else 1)' "${SITE_CONFIG}"; then
	echo "[site-setup] creating the site's encryption key"
	# `--`: a Fernet key is URL-safe base64 and can start with "-", which bench would read as an option
	bench --site "${SITE_NAME}" set-config encryption_key -- \
		"$(./env/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
fi

installed_now=0
installed="$(bench --site "${SITE_NAME}" list-apps | awk '{print $1}')"  # one bench start-up, not one per app
for app in ${APPS}; do
	if printf '%s\n' "${installed}" | grep -qx "${app}"; then
		echo "[site-setup] ${app} already installed"
	else
		echo "[site-setup] installing ${app}"
		bench --site "${SITE_NAME}" install-app "${app}"
		installed_now=1
	fi
done

# A DOMAIN of ":<port>" (Caddy's "any host" form) names no host, so it sets no host_name.
if [ -n "${DOMAIN:-}" ] && [ "${DOMAIN#:}" = "${DOMAIN}" ]; then
	# DOMAIN may carry an explicit scheme (http://host serves plain HTTP); otherwise Caddy serves HTTPS.
	case "${DOMAIN}" in
		http://*|https://*) HOST_URL="${DOMAIN}" ;;
		*) HOST_URL="https://${DOMAIN}" ;;
	esac
	bench --site "${SITE_NAME}" set-config host_name -- "${HOST_URL}"
fi

MIGRATED="sites/${SITE_NAME}/.migrated-fingerprint"
if [ "${FORCE_MIGRATE:-0}" != "1" ] && [ "${installed_now}" = "0" ] && [ -s "${FINGERPRINT}" ] \
	&& [ -f "${MIGRATED}" ] && cmp -s "${FINGERPRINT}" "${MIGRATED}"; then
	echo "[site-setup] nothing that changes the database changed since the last migrate ($(cut -c1-12 "${FINGERPRINT}")): skipping migrate"
else
	echo "[site-setup] migrating"
	bench --site "${SITE_NAME}" migrate
	if [ -s "${FINGERPRINT}" ]; then cp "${FINGERPRINT}" "${MIGRATED}"; fi
fi
bench --site "${SITE_NAME}" clear-cache

echo "[site-setup] done"
touch "${MARKER}"
