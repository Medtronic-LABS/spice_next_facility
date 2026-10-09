#!/bin/bash
# Run the production stack (Caddy + app + MariaDB, deploy/docker-compose.yml) on this machine, built from
# the working tree, before pushing. Builds natively (arm64 on Apple Silicon), so the result differs from
# EC2 only in ports, HTTPS and the SSH deploy step.
#
#   deploy/local-test.sh up [--rebuild-base]   build the images (base only if missing) and start the stack
#   deploy/local-test.sh down                  stop it and delete its data (fresh site next time)
#   deploy/local-test.sh logs | status
#
# LOCAL_PORT (default 8088) and LOCAL_APP_PORT (default 18000) pick the host ports.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(dirname "${DEPLOY_DIR}")"
PROJECT=spice-facility-local
BASE_IMAGE=ghcr.io/medtronic-labs/spice_next_facility-base:local
APP_IMAGE=ghcr.io/medtronic-labs/spice_next_facility:local
ENV_FILE="${DEPLOY_DIR}/.env.local"
export LOCAL_PORT="${LOCAL_PORT:-8088}" LOCAL_APP_PORT="${LOCAL_APP_PORT:-18000}"

compose() {
	docker compose -p "${PROJECT}" --env-file "${ENV_FILE}" \
		-f "${DEPLOY_DIR}/docker-compose.yml" -f "${DEPLOY_DIR}/docker-compose.local.yml" "$@"
}

write_env() {
	[ -f "${ENV_FILE}" ] && return 0
	cat > "${ENV_FILE}" <<'EOF'
# Local trial only (gitignored). Delete it to regenerate; `down` keeps it.
IMAGE_TAG=local
GHCR_OWNER=medtronic-labs
SITE_NAME=spice-facility-local.localhost
DOMAIN=:80
ADMIN_PASSWORD=admin
DB_ROOT_PASSWORD=local-root
DB_BUFFER_POOL=512M
SPICE_AI_OLLAMA_ENABLED=0
SPICE_AI_OLLAMA_URL=
BACKUP_S3_BUCKET=
AWS_DEFAULT_REGION=ap-south-1
BACKUP_RETENTION_DAYS=7
ALERT_WEBHOOK_URL=
EOF
	echo "[local] wrote ${ENV_FILE}"
}

build_base() {
	if [ "${1:-}" != "--rebuild-base" ] && docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
		echo "[local] reusing ${BASE_IMAGE} (pass --rebuild-base after changing docker/base/)"
		return 0
	fi
	echo "[local] building the base image (first time ~5-10 min)"
	set -a
	# shellcheck disable=SC1091
	. "${APP_DIR}/docker/base/versions.env"
	set +a
	docker build -f "${APP_DIR}/docker/base/Dockerfile" \
		--build-arg BENCH_IMAGE --build-arg FRAPPE_REF --build-arg ERPNEXT_REF \
		--build-arg HEALTHCARE_REF --build-arg FRAPPE_THEME_REF \
		-t "${BASE_IMAGE}" "${APP_DIR}/docker/base"
}

# Same layout as CI (context root holding apps/spice_facility), staged from the tracked and untracked,
# non-ignored files of the working tree, so uncommitted changes are tested and local clutter is not.
build_app() {
	BUILD_CTX="$(mktemp -d)"
	trap 'rm -rf "${BUILD_CTX}"' EXIT
	mkdir -p "${BUILD_CTX}/apps/spice_facility"
	git -C "${APP_DIR}" ls-files -z --cached --others --exclude-standard \
		| rsync -a --from0 --files-from=- "${APP_DIR}/" "${BUILD_CTX}/apps/spice_facility/"
	echo "[local] building the app image"
	docker build -f "${BUILD_CTX}/apps/spice_facility/docker/allinone/Dockerfile" \
		--build-arg BASE_IMAGE="${BASE_IMAGE}" -t "${APP_IMAGE}" "${BUILD_CTX}"
}

wait_ready() {
	echo "[local] waiting for the site (a fresh one installs 5 apps, ~2-10 min)"
	for _ in $(seq 1 90); do
		if curl -fsS "http://localhost:${LOCAL_PORT}/api/method/ping" >/dev/null 2>&1; then
			echo "[local] ready: http://localhost:${LOCAL_PORT}  (Administrator / password in deploy/.env.local)"
			return 0
		fi
		sleep 10
	done
	echo "[local] not ready after 15 min; see: deploy/local-test.sh logs" >&2
	return 1
}

case "${1:-up}" in
	up)
		write_env
		build_base "${2:-}"
		build_app
		compose up -d --remove-orphans
		wait_ready
		;;
	down) compose down -v ;;
	logs) compose logs -f app ;;
	status)
		compose ps
		docker exec spice-next-facility-local supervisorctl status
		;;
	*)
		echo "usage: $0 [up [--rebuild-base] | down | logs | status]" >&2
		exit 2
		;;
esac
