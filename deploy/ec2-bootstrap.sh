#!/bin/bash
# One-time preparation of a fresh Ubuntu 24.04 EC2 instance for spice_next_facility. Run as the default
# `ubuntu` user (it uses sudo):  bash ec2-bootstrap.sh
# Installs Docker Engine + Compose plugin, adds swap, and creates the deploy directory the pipeline uses.
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/home/ubuntu/spice_next_facility}"
SWAP_GB="${SWAP_GB:-4}"

echo "[bootstrap] installing Docker"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "${VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$(whoami)"
sudo systemctl enable --now docker

if ! swapon --show | grep -q /swapfile; then
	echo "[bootstrap] adding ${SWAP_GB}G swap (bench migrate and asset-heavy restarts spike memory)"
	sudo fallocate -l "${SWAP_GB}G" /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

mkdir -p "${DEPLOY_PATH}"
echo "[bootstrap] done. Log out and back in (docker group), then let the pipeline deploy into ${DEPLOY_PATH}."
