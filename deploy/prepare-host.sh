#!/bin/bash
# Runs ON the host (invoked over SSH by the deploy job, before deploy.sh). Makes a fresh Ubuntu host ready:
# Docker Engine + compose plugin from Ubuntu's own signed archive (no curl | sh), the deploy user in the
# docker group, and a swap file when the host has none. Idempotent: a ready host is left untouched.
# Needs passwordless sudo only when something is missing.
set -euo pipefail

SWAP_FILE=/swapfile
SWAP_SIZE="${SWAP_SIZE:-4G}"

log() { echo "[prepare-host] $*"; }

need_sudo() {
	if ! sudo -n true 2>/dev/null; then
		echo "[prepare-host] $1 needs passwordless sudo for $(id -un); do it by hand (docs/deployment/aws-ec2.md, section 2)" >&2
		exit 1
	fi
}

ensure_docker() {
	if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
		log "docker present: $(docker --version)"
		return
	fi
	need_sudo "installing Docker"
	if ! command -v apt-get >/dev/null 2>&1; then
		echo "[prepare-host] no apt-get: automatic install supports Ubuntu only; install Docker + compose by hand" >&2
		exit 1
	fi
	log "installing docker.io + docker-compose-v2 from the Ubuntu archive"
	sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 update -q
	sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y -q docker.io docker-compose-v2
	sudo systemctl enable --now docker
	log "installed: $(docker --version)"
}

# Group membership applies from the next SSH login, which is the deploy step's own connection.
ensure_docker_group() {
	local user
	user="$(id -un)"
	if id -nG "${user}" | tr ' ' '\n' | grep -qx docker; then
		return
	fi
	need_sudo "adding ${user} to the docker group"
	sudo usermod -aG docker "${user}"
	log "added ${user} to the docker group"
}

# A single small host runs MariaDB and Frappe together; swap absorbs short memory peaks (migrate, builds).
ensure_swap() {
	if [ -n "$(swapon --noheadings --show 2>/dev/null)" ]; then
		return
	fi
	need_sudo "creating swap"
	log "creating ${SWAP_SIZE} swap at ${SWAP_FILE}"
	sudo fallocate -l "${SWAP_SIZE}" "${SWAP_FILE}"
	sudo chmod 600 "${SWAP_FILE}"
	sudo mkswap "${SWAP_FILE}" >/dev/null
	sudo swapon "${SWAP_FILE}"
	if ! grep -q "^${SWAP_FILE} " /etc/fstab; then
		echo "${SWAP_FILE} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
	fi
}

ensure_docker
ensure_docker_group
ensure_swap
log "host ready"
