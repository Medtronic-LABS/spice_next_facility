#!/bin/bash
# Runs ON the EC2 host (invoked over SSH by the deploy job). Expects deploy/.env already written by the
# job with IMAGE_TAG set to the build to roll out. Pulls it, (re)starts the stack, waits for the site to
# answer, and on failure rolls back to the previously running image tag.
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.yml"
LAST_GOOD_FILE=.last_good_image_tag
HEALTH_TRIES="${HEALTH_TRIES:-90}"   # x10s: a first deploy creates the site and installs 4 apps

# .env is data for Docker Compose, not a shell script: read the two values needed here instead of
# sourcing it, so a secret's characters can never run as shell on the host.
env_value() {
	{ grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2- | sed -e "s/^'//" -e "s/'\$//"
}
IMAGE_TAG="$(env_value IMAGE_TAG)"
SITE_NAME="$(env_value SITE_NAME)"
: "${IMAGE_TAG:?IMAGE_TAG missing from .env}" "${SITE_NAME:?SITE_NAME missing from .env}"

healthy() {
	curl -fsS -H "Host: ${SITE_NAME}" "http://127.0.0.1:8000/api/method/ping" >/dev/null 2>&1
}

wait_healthy() {
	for _ in $(seq 1 "${HEALTH_TRIES}"); do
		if healthy; then
			return 0
		fi
		sleep 10
	done
	return 1
}

# The last tag that passed the health check on this host; the rollback target if this deploy fails.
previous=$(cat "${LAST_GOOD_FILE}" 2>/dev/null || true)

echo "[deploy] pulling ${IMAGE_TAG}"
${COMPOSE} pull app
${COMPOSE} up -d --remove-orphans

echo "[deploy] waiting for ${SITE_NAME} (first deploy can take ~10 minutes)"
if wait_healthy; then
	echo "[deploy] healthy on ${IMAGE_TAG}"
	echo "${IMAGE_TAG}" > "${LAST_GOOD_FILE}"
	docker image prune -f >/dev/null
	exit 0
fi

echo "[deploy] health check failed for ${IMAGE_TAG}" >&2
docker logs --tail 80 spice-next-facility >&2 || true
if [ -n "${previous}" ] && [ "${previous}" != "${IMAGE_TAG}" ]; then
	echo "[deploy] rolling back to ${previous}" >&2
	sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG='${previous}'/" .env
	IMAGE_TAG="${previous}" ${COMPOSE} up -d app
	if wait_healthy; then
		echo "[deploy] rolled back to ${previous}" >&2
	else
		echo "[deploy] rollback to ${previous} is not healthy either" >&2
	fi
fi
exit 1
