#!/bin/bash
# Container entrypoint (root). Every boot: put build-time generated files back into the (volume-mounted)
# sites/ directory, write common_site_config.json from runtime env, then start supervisord.
# With arguments, runs them instead of supervisord (CI uses this to run tests in the shipped image).
set -eu

DB_HOST="${DB_HOST:-mariadb}"
DB_PORT="${DB_PORT:-3306}"
SITES=/home/frappe/frappe-bench/sites

echo "[entrypoint] re-materializing sites/ from the build-time seed"
mkdir -p "${SITES}"
cp -f /home/frappe/sites-seed/apps.txt  "${SITES}/apps.txt"
cp -f /home/frappe/sites-seed/apps.json "${SITES}/apps.json"
# Assets are code-derived and their hashed names change every build: always replace them.
rm -rf "${SITES}/assets"
cp -r /home/frappe/sites-seed/assets "${SITES}/assets"

# MariaDB client TLS for every client bench shells out to (mariadb, mariadb-dump, mysqladmin):
#   off     the bundled MariaDB container on a private Docker network (offers no TLS)
#   verify  an external server (e.g. RDS): TLS required and the certificate checked, against DB_SSL_CA if set
DB_TLS="${DB_TLS:-off}"
echo "[entrypoint] MariaDB client TLS: ${DB_TLS}"
case "${DB_TLS}" in
	off) printf '[client]\nskip-ssl\n' > /etc/mysql/conf.d/spice-client.cnf ;;
	verify)
		printf '[client]\nssl\nssl-verify-server-cert\n' > /etc/mysql/conf.d/spice-client.cnf
		if [ -n "${DB_SSL_CA:-}" ]; then
			printf 'ssl-ca=%s\n' "${DB_SSL_CA}" >> /etc/mysql/conf.d/spice-client.cnf
		fi
		;;
	*)
		echo "[entrypoint] DB_TLS must be 'off' or 'verify', got '${DB_TLS}'" >&2
		exit 1
		;;
esac

echo "[entrypoint] writing common_site_config.json"
cat > "${SITES}/common_site_config.json" <<JSON
{
  "db_host": "${DB_HOST}",
  "db_port": ${DB_PORT},
  "redis_cache": "redis://127.0.0.1:6379/0",
  "redis_queue": "redis://127.0.0.1:6379/1",
  "redis_socketio": "redis://127.0.0.1:6379/2",
  "socketio_port": 9000,
  "default_site": "${SITE_NAME:-}",
  "spice_ai_ollama_enabled": ${SPICE_AI_OLLAMA_ENABLED:-0},
  "spice_ai_ollama_url": "${SPICE_AI_OLLAMA_URL:-}"
}
JSON

chown -R frappe:frappe "${SITES}"

if [ "$#" -gt 0 ]; then
	exec "$@"
fi
exec supervisord -c /etc/supervisor/supervisord.conf
