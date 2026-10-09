#!/bin/bash
# One-shot (supervisord program `site-setup`, also run directly by CI): create the site if missing,
# install every app if missing (dependency order), always migrate + clear cache, then drop the marker
# that wait-for-site.sh waits for. Safe to automate because this deployment is single-instance.
#
# DB_ROOT_PASSWORD is used only by `bench new-site` (to create the site's own database and scoped user);
# afterwards the site connects with the scoped credentials bench writes into site_config.json.
set -eu

: "${SITE_NAME:?SITE_NAME must be set}"
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
DB_HOST="${DB_HOST:-mariadb}"
DB_PORT="${DB_PORT:-3306}"
APPS="erpnext healthcare frappe_theme spice_facility"

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

for app in ${APPS}; do
	if bench --site "${SITE_NAME}" list-apps | awk '{print $1}' | grep -qx "${app}"; then
		echo "[site-setup] ${app} already installed"
	else
		echo "[site-setup] installing ${app}"
		bench --site "${SITE_NAME}" install-app "${app}"
	fi
done

if [ -n "${DOMAIN:-}" ]; then
	# DOMAIN may carry an explicit scheme (http://host serves plain HTTP); otherwise Caddy serves HTTPS.
	case "${DOMAIN}" in
		http://*|https://*) HOST_URL="${DOMAIN}" ;;
		*) HOST_URL="https://${DOMAIN}" ;;
	esac
	bench --site "${SITE_NAME}" set-config host_name "${HOST_URL}"
fi

echo "[site-setup] migrating"
bench --site "${SITE_NAME}" migrate
bench --site "${SITE_NAME}" clear-cache

echo "[site-setup] done"
touch "${MARKER}"
