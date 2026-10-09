#!/bin/bash
# Daily backup (supervisord program `backup`): `bench backup --with-files` (database + public/private
# files, compressed), copied to S3 when BACKUP_S3_BUCKET is set. On EC2 the AWS CLI uses the instance
# role (attach one with s3:PutObject on the bucket); no access keys are stored in the image or .env.
set -u

: "${SITE_NAME:?SITE_NAME must be set}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_DIR="/home/frappe/frappe-bench/sites/${SITE_NAME}/private/backups"

alert() {
	echo "[backup] ALERT: $1" >&2
	if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
		curl -sf -X POST -H "Content-Type: application/json" \
			-d "{\"text\": \"spice_next_facility backup (${SITE_NAME}): $1\"}" "${ALERT_WEBHOOK_URL}" \
			|| echo "[backup] alert webhook call failed" >&2
	fi
}

cd /home/frappe/frappe-bench
while true; do
	stamp=$(date +%Y%m%d-%H%M%S)
	if bench --site "${SITE_NAME}" backup --with-files --compress; then
		echo "[backup] ${stamp} local backup written to ${BACKUP_DIR}"
		if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
			if aws s3 sync "${BACKUP_DIR}" "s3://${BACKUP_S3_BUCKET}/${SITE_NAME}/" --only-show-errors; then
				echo "[backup] ${stamp} synced to s3://${BACKUP_S3_BUCKET}/${SITE_NAME}/"
			else
				alert "S3 sync to s3://${BACKUP_S3_BUCKET} failed at ${stamp}; backup is local only"
			fi
		else
			echo "[backup] BACKUP_S3_BUCKET not set; backup kept on the instance only" >&2
		fi
		find "${BACKUP_DIR}" -type f -mtime "+${RETENTION_DAYS}" -delete
	else
		alert "bench backup failed at ${stamp}"
	fi
	sleep "${INTERVAL}"
done
