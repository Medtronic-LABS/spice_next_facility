# Deploying spice_next_facility on AWS EC2

| Branch | GitHub environment | URL | EC2 |
|---|---|---|---|
| `develop` | `dev` | https://spice-facility-dev.labsplatform.com | its own instance |
| `training` | `training` | https://spice-facility-training.labsplatform.com | its own instance |
| `main`, tags `v*` | — | build, test and publish only | — |

`http://` redirects to `https://` once Caddy has the certificate.

**Branch flow:** feature branch → PR into `develop` (tests run) → merge = deploy to dev → PR `develop` →
`training` → merge = deploy to training → PR `training` → `main` for a release (tag `vX.Y.Z` to version it).

Each EC2 instance runs three containers (`deploy/docker-compose.yml`):

| Container | What | Exposed |
|---|---|---|
| `caddy` | HTTPS entry point (Let's Encrypt, auto-renewed) | 80, 443 |
| `app` | all-in-one image: gunicorn, socket.io, workers (incl. a dedicated AI `long` worker), scheduler, Redis, daily backup | 127.0.0.1:8000 only |
| `mariadb` | MariaDB 10.6, data on the instance's EBS volume | not exposed |

The pipeline (`.github/workflows/docker-publish.yml`) **runs the test suite inside the exact image against
MariaDB** before publishing it to `ghcr.io/medtronic-labs/spice_next_facility`, then deploys it over SSH
with an automatic rollback to the last healthy build if the new one does not come up.

### Images: a reusable base + a thin app layer

| Image | Contents | Rebuilt when |
|---|---|---|
| `spice_next_facility-base:<key>` | bench, Frappe, ERPNext, Frappe Health, frappe_theme, assets built | `docker/base/versions.env` or `docker/base/Dockerfile` changes |
| `spice_next_facility:<tag>` | the base + `spice_facility` | every push |

A push that changes only `spice_facility` reuses the base (the base job is a no-op), builds a few small
layers, and the EC2 pulls only those. The base is pulled once per version bump and cached on each host.

**Upgrading Frappe / ERPNext / Health / frappe_theme:** edit the release tags in
`docker/base/versions.env` (bump `BASE_REVISION` to force a rebuild without a version change), open a PR
into `develop`, merge. That run builds the new base once (~30 min), every later run reuses it. Run the
workflow manually with **rebuild_base** to rebuild it on demand.

No local AI model runs here (`SPICE_AI_OLLAMA_ENABLED=0`): AI summaries are off; Ask Data works with
Claude or OpenAI, which see only the question and field names, and answers are written from the results.

### Trying the stack locally

`deploy/local-test.sh up` runs this exact stack (same compose file, Caddyfile, image build and site setup)
on your machine from the working tree, uncommitted changes included, at http://localhost:8088. The first run
builds the base (~5–10 min); later runs reuse it and rebuild only the app layer. It differs from EC2 only in
ports (8088 / 18000, set by `LOCAL_PORT` / `LOCAL_APP_PORT`; a dev bench usually holds 8000), plain HTTP and
the SSH deploy. Settings live in `deploy/.env.local` (gitignored, written on first run; Administrator /
`admin`). `deploy/local-test.sh down` removes it with its data; `logs` and `status` inspect it. After changing
`docker/base/`, pass `up --rebuild-base`.

---

## 1. AWS resources (once)

Do this **once per environment** (dev and training each get their own instance, DNS record and secrets).

1. **EC2 instance** — Ubuntu 24.04 LTS, `t3.xlarge` (4 vCPU / 16 GB) recommended for ERPNext + Health +
   MariaDB on one host (`t3.large` / 8 GB works for a small pilot). **EBS gp3, 60 GB** or more.
2. **Elastic IP** attached to the instance (the DNS record and `DEPLOY_HOST` must not change).
3. **Security group**
   - 80, 443 from `0.0.0.0/0` (Caddy).
   - 22 from GitHub Actions runners (deploy) and your admin IPs. GitHub-hosted runners have no fixed IP
     range you can allowlist cheaply; either accept 22 open with **key-only auth** (password auth is off
     on Ubuntu EC2 images) or use a self-hosted runner / switch the deploy to SSM later.
4. **S3 bucket** for backups (e.g. `spice-facility-backups-<env>`), versioning on, a lifecycle rule to
   expire old objects, public access blocked.
5. **IAM role** attached to the instance with `s3:PutObject`, `s3:ListBucket` on that bucket. The backup
   job uses it through the instance metadata service — no AWS keys are stored anywhere. Containers sit on
   a Docker bridge, so set the metadata hop limit to 2:
   `aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required`
6. **DNS**: an `A` record per environment → that instance's Elastic IP:
   `spice-facility-dev.labsplatform.com` (dev) and `spice-facility-training.labsplatform.com` (training).
   Caddy needs it, plus ports 80/443 open, to obtain the Let's Encrypt certificate.

## 2. Prepare the instance (once)

```bash
scp deploy/ec2-bootstrap.sh ubuntu@<elastic-ip>:~
ssh ubuntu@<elastic-ip> 'bash ec2-bootstrap.sh'   # Docker + Compose, 4 GB swap, /home/ubuntu/spice_next_facility
```

Create a **deploy key pair** for GitHub (`ssh-keygen -t ed25519 -f deploy_key -N ""`), append
`deploy_key.pub` to `/home/ubuntu/.ssh/authorized_keys`, and keep the private key for the secret below.

## 3. GitHub setup (once)

Repository: `Medtronic-LABS/spice_next_facility` (this app's source, Python package `spice_facility`).

Environments **`dev`** and **`training`** exist with their variables already set (Settings → Environments).
Optionally add required reviewers (e.g. on `training`) so a deploy waits for approval. Add these
**to each environment**, with that environment's instance and passwords:

**Secrets**

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Elastic IP or hostname of the instance |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | private key of the deploy key pair |
| `DEPLOY_PORT` | optional, default 22 |
| `ADMIN_PASSWORD` | Administrator password (used when the site is first created) |
| `DB_ROOT_PASSWORD` | MariaDB root password (long, random; never reused) |
| `BACKUP_S3_BUCKET` | backup bucket name (empty = backups stay on the instance) |
| `ALERT_WEBHOOK_URL` | optional Slack/Teams webhook for backup failures |
| `SPICE_AI_ANTHROPIC_API_KEY` | optional Claude key for Ask Data |
| `SPICE_AI_OPENAI_API_KEY` | optional OpenAI key for Ask Data |
| `GHCR_PULL_USER`, `GHCR_PULL_TOKEN` | if the GHCR package is private: a user and a PAT with `read:packages` |

**Variables**

| Variable | dev | training |
|---|---|---|
| `SITE_NAME` (Frappe site name) | `spice-facility-dev.labsplatform.com` | `spice-facility-training.labsplatform.com` |
| `DOMAIN` (bare host = HTTPS; `http://host` = plain HTTP) | same as `SITE_NAME` | same as `SITE_NAME` |
| `SITE_URL` (link shown on the deploy) | `https://spice-facility-dev.labsplatform.com` | `https://spice-facility-training.labsplatform.com` |
| `DEPLOY_PATH` | `/home/ubuntu/spice_next_facility` |
| `AWS_DEFAULT_REGION` | `ap-south-1` |

The image is pushed with the workflow's own `GITHUB_TOKEN` (`packages: write`) — no registry secret needed
for publishing.

## 4. First deploy

Push to `develop` (dev) or `training` (training), or run the workflow manually on that branch. The deploy job:

1. copies `docker-compose.yml`, `Caddyfile`, `deploy.sh` to `DEPLOY_PATH`,
2. writes `.env` from the secrets (CI-owned; do not edit it on the host),
3. runs `deploy.sh`: pull the `sha-<commit>` image → `docker compose up -d` → wait for
   `/api/method/ping` (a first deploy creates the site and installs ERPNext, Health, frappe_theme and
   spice_facility — allow ~10 minutes) → on failure, roll back to the last healthy tag.

Then open the environment's URL, log in as Administrator and complete the **ERPNext setup wizard** (company,
currency, fiscal year). Health's masters (departments, practitioners, service units) come next; demo data
can be loaded with `bench --site <SITE_NAME> execute spice_facility.demo.seed.run` (non-production only).

To use Ask Data with Claude/OpenAI, open **SPICE AI Settings**, enable the provider, set the model and use
**Test** — keys supplied as secrets above are picked up automatically.

## 5. Every later deploy

Merge to `develop` / `training`. Tests must pass before an image is pushed; `site-setup` runs
`bench migrate` on start (single instance, so there is no migration race).

Run a specific earlier build: re-run the deploy job of that commit's workflow run, or on the host set
`IMAGE_TAG=sha-<commit>` in `.env` and run `./deploy.sh`.

## 6. Operations

```bash
cd /home/ubuntu/spice_next_facility
docker compose ps
docker compose logs -f app                                       # supervisord programs
docker exec -it spice-next-facility tail -f /var/log/supervisor/backend.err.log
docker exec -it spice-next-facility supervisorctl status
docker exec -it -u frappe spice-next-facility bash -lc 'cd ~/frappe-bench && bench --site "$SITE_NAME" migrate'
```

**Backups**: daily `bench backup --with-files` into the `sites` volume, synced to
`s3://<BACKUP_S3_BUCKET>/<SITE_NAME>/`, local copies kept `BACKUP_RETENTION_DAYS` (7). Restore:

```bash
aws s3 sync s3://<bucket>/<site>/ ./restore/
docker cp ./restore spice-next-facility:/tmp/restore
docker exec -it -u frappe spice-next-facility bash -lc 'cd ~/frappe-bench && \
  bench --site "$SITE_NAME" restore /tmp/restore/<timestamp>-database.sql.gz \
  --with-public-files /tmp/restore/<timestamp>-files.tar --with-private-files /tmp/restore/<timestamp>-private-files.tar \
  --db-root-password "$DB_ROOT_PASSWORD"'
```

Also enable **EBS snapshots** (AWS Data Lifecycle Manager, daily) as a second, whole-volume safety net.

## Next steps when this outgrows one host

Move MariaDB to Amazon RDS (set `DB_HOST` and drop the `mariadb` service), put an ALB + ACM in front
instead of Caddy, and replace SSH deploys with SSM Run Command via GitHub OIDC.
