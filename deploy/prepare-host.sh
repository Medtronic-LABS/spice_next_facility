#!/bin/bash
# Runs ON the host (invoked over SSH by the deploy job, before deploy.sh). Makes a fresh Ubuntu host ready:
# the data volume mounted at DATA_DIR, Docker Engine + compose plugin from Ubuntu's own signed archive (no
# curl | sh), the deploy user in the docker group, and a swap file when the host has none. Idempotent: a
# ready host is left untouched. Needs passwordless sudo only when something is missing.
#
#   DATA_DIR        where the database and sites/ live; must be a separately mounted volume
#   DATA_VOLUME_ID  the attached EBS volume (vol-...) to mount there when DATA_DIR is not mounted yet
set -euo pipefail

DATA_DIR="${DATA_DIR:?DATA_DIR must be set}"
DATA_VOLUME_ID="${DATA_VOLUME_ID:-}"
SWAP_FILE=/swapfile
SWAP_SIZE="${SWAP_SIZE:-4G}"

log() { echo "[prepare-host] $*"; }

need_sudo() {
	if ! sudo -n true 2>/dev/null; then
		echo "[prepare-host] $1 needs passwordless sudo for $(id -un); do it by hand (docs/deployment/aws-ec2.md, section 2)" >&2
		exit 1
	fi
}

fail() {
	echo "[prepare-host] $*" >&2
	exit 1
}

# EBS volumes on Nitro instances appear as NVMe disks whose by-id name carries the volume id.
volume_device() {
	local dev="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}"
	[ -e "${dev}" ] || fail "volume ${DATA_VOLUME_ID} is not attached to this instance (looked for ${dev})"
	readlink -f "${dev}"
}

# Mounts the data volume at DATA_DIR (by filesystem UUID, in fstab). A disk is formatted only when it carries
# no signature at all (no filesystem, no partition table); anything else is left alone and reported.
ensure_data_mount() {
	if mountpoint -q "${DATA_DIR}"; then
		log "data volume mounted at ${DATA_DIR}: $(df -h --output=source,size,avail "${DATA_DIR}" | tail -n 1)"
		return
	fi
	[ -n "${DATA_VOLUME_ID}" ] || fail "${DATA_DIR} is not a mounted volume: attach an EBS volume and set DATA_VOLUME_ID (docs/deployment/aws-ec2.md, section 1)"
	need_sudo "mounting the data volume"
	local dev fstype uuid
	dev="$(volume_device)"
	if [ -n "$(lsblk -n -o MOUNTPOINTS "${dev}" | tr -d '[:space:]')" ]; then
		fail "${DATA_VOLUME_ID} (${dev}) is already mounted elsewhere; refusing to touch it"
	fi
	if [ -z "$(sudo wipefs --no-act --noheadings "${dev}")" ]; then
		log "formatting blank volume ${DATA_VOLUME_ID} (${dev}) as ext4"
		sudo mkfs.ext4 -q -L spice-data "${dev}"
	fi
	fstype="$(sudo blkid -o value -s TYPE "${dev}" || true)"
	[ "${fstype}" = "ext4" ] || fail "${DATA_VOLUME_ID} (${dev}) holds '${fstype:-a partition table}', not an ext4 filesystem; refusing to format it"
	uuid="$(sudo blkid -o value -s UUID "${dev}")"
	sudo mkdir -p "${DATA_DIR}"
	# While unmounted the directory is immutable, so nothing can write the database onto the root disk.
	sudo chattr +i "${DATA_DIR}"
	if ! grep -q "^UUID=${uuid} " /etc/fstab; then
		echo "UUID=${uuid} ${DATA_DIR} ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2" | sudo tee -a /etc/fstab >/dev/null
	fi
	sudo systemctl daemon-reload
	sudo mount "${DATA_DIR}"
	log "mounted ${DATA_VOLUME_ID} at ${DATA_DIR}"
}

# Docker waits for the data volume at boot: if it is missing, the containers stay down instead of starting
# against an empty directory and creating a fresh database and site.
ensure_docker_waits_for_data() {
	local dropin=/etc/systemd/system/docker.service.d/spice-data.conf
	local want="[Unit]
RequiresMountsFor=${DATA_DIR}"
	if [ "$(cat "${dropin}" 2>/dev/null)" = "${want}" ]; then
		return
	fi
	need_sudo "making Docker wait for ${DATA_DIR}"
	sudo mkdir -p "$(dirname "${dropin}")"
	printf '%s\n' "${want}" | sudo tee "${dropin}" >/dev/null
	sudo systemctl daemon-reload
	log "docker.service now requires ${DATA_DIR}"
}

ensure_data_dirs() {
	local d
	for d in mariadb sites; do
		if [ ! -d "${DATA_DIR}/${d}" ]; then
			need_sudo "creating ${DATA_DIR}/${d}"
			sudo mkdir -p "${DATA_DIR}/${d}"
		fi
	done
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

ensure_data_mount
ensure_data_dirs
ensure_docker
ensure_docker_waits_for_data
ensure_docker_group
ensure_swap
log "host ready"
