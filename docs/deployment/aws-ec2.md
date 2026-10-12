# Deploying spice_next_facility on AWS EC2

| Branch | GitHub environment | URL | EC2 |
|---|---|---|---|
| `develop` | `dev` | https://spice-next-facility-dev.labsplatform.com | its own instance |
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
| `mariadb` | MariaDB 10.6 | not exposed |

**Data lives on its own EBS volume**, mounted at `DATA_DIR` (default `/srv/spice-data`): the database in
`mariadb/` and Frappe's `sites/` (site config, uploaded files, local backups) in `sites/`. The root disk holds
only the OS, Docker images and Caddy's certificates, so it can fill up or be replaced without touching data,
and the data volume can be snapshotted, resized or moved to a new instance on its own. `deploy.sh` refuses to
start the stack if `DATA_DIR` is not a mounted volume, and Docker will not start at boot without it.

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

**Trust.** Only pushes to `develop`, `training` or `main` publish a base, with a signed build-provenance
attestation. A run reuses a published base only if `gh attestation verify` proves this workflow built it
from one of those branches. A PR or tag that changes `docker/base/` builds the base inside its own job and
publishes nothing from it. The app image is built once, tested, and that same image is pushed (only from
`develop`, `training`, `main` and `v*` tags) with its own attestation; the deploy job verifies that attestation
against the branch being deployed and runs the image **by digest**, so a tag moved later cannot change what
runs. Checkouts keep no credentials and `.git` never enters the image (`docker/allinone/Dockerfile.dockerignore`).
The deploy uses the runner's own OpenSSH with the host key pinned (`StrictHostKeyChecking=yes`); `.env` is
copied as a 0600 file, so no secret appears on a command line.

**Upgrading Frappe / ERPNext / Health / frappe_theme:** in `docker/base/versions.env` change the release
tag **and** the commit it resolves to (`git ls-remote https://github.com/<repo>.git 'refs/tags/<tag>*'`; the
build fails if a tag no longer matches its pinned commit). `BENCH_IMAGE` is pinned by digest
(`docker buildx imagetools inspect frappe/bench:<tag>`). Bump `BASE_REVISION` to force a rebuild without a
version change. Open a PR into `develop`, merge. That run builds the new base once (~30 min), every later run reuses it. Run the
workflow manually with **rebuild_base** to rebuild it on demand.

No local AI model runs here (`SPICE_AI_OLLAMA_ENABLED=0`): Ask Data works with Claude, OpenAI or Gemini, which
see only the question and field names, and answers are written from the results. AI summaries are off unless a
System Manager ticks **Allow cloud models to read patient records** and picks a cloud **Summary provider** in SPICE
AI Settings (the record is sent without name, contact or insurance details — see the README's data policy).

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
   MariaDB on one host (`t3.large` / 8 GB works for a small pilot). Root disk **gp3, 30 GB** (OS + images).
   A Nitro instance type (t3, t3a, m5, m6i, …): the data volume is found by its id as an NVMe device.
2. **Data volume** — a separate **EBS gp3** volume (20 GB is plenty for dev; size training/production for
   the database plus attachments), encrypted, in the **same availability zone** as the instance, attached to it
   (any device name). Put its id in the `DATA_VOLUME_ID` variable. The first deploy formats it (only if it
   is completely blank — a volume with any filesystem or partition table is never formatted) and mounts it
   at `DATA_DIR` through `/etc/fstab`. Snapshot this volume (see Operations).
3. **Elastic IP** attached to the instance (the DNS record and `DEPLOY_HOST` must not change).
4. **Security group**
   - 80, 443 from `0.0.0.0/0` (Caddy).
   - 22 from GitHub Actions runners (deploy) and your admin IPs. GitHub-hosted runners have no fixed IP
     range you can allowlist cheaply; either accept 22 open with **key-only auth** (password auth is off
     on Ubuntu EC2 images) or use a self-hosted runner / switch the deploy to SSM later.
5. **S3 bucket** for backups (e.g. `spice-facility-backups-<env>`), versioning on, a lifecycle rule to
   expire old objects, public access blocked.
6. **IAM role** attached to the instance with `s3:PutObject`, `s3:ListBucket` on that bucket. The backup
   job uses it through the instance metadata service — no AWS keys are stored anywhere. Containers sit on
   a Docker bridge, so set the metadata hop limit to 2:
   `aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required`
7. **DNS**: an `A` record per environment → that instance's Elastic IP:
   `spice-next-facility-dev.labsplatform.com` (dev) and `spice-facility-training.labsplatform.com` (training).
   Caddy needs it, plus ports 80/443 open, to obtain the Let's Encrypt certificate.

## 2. Prepare the instance (once)

Nothing to install by hand on Ubuntu: every deploy runs `deploy/prepare-host.sh` on the host first. It
mounts the data volume at `DATA_DIR` (and makes Docker wait for it at boot), installs Docker + the compose plugin from Ubuntu's archive (`docker.io`, `docker-compose-v2`), adds the deploy
user to the `docker` group and creates a 4 GB swap file if the host has none; on a ready host it changes
nothing. This needs passwordless `sudo` for the deploy user (the default on EC2's `ubuntu` user). Without it,
or on a non-Ubuntu host, run the same script yourself once as a user who can sudo:

```bash
scp deploy/prepare-host.sh ubuntu@<elastic-ip>:~
ssh ubuntu@<elastic-ip> 'DATA_DIR=/srv/spice-data DATA_VOLUME_ID=vol-… bash prepare-host.sh'
```

Create a **deploy key pair** for GitHub (`ssh-keygen -t ed25519 -f deploy_key -N ""`), append
`deploy_key.pub` to `/home/ubuntu/.ssh/authorized_keys`, and keep the private key for the secret below.

## 3. GitHub setup (once)

Repository: `Medtronic-LABS/spice_next_facility` (this app's source, Python package `spice_facility`).

Environments **`dev`** and **`training`** exist with their variables already set (Settings → Environments).
Each may deploy only from its own branch (deployment branch policy: `dev` ← `develop`, `training` ←
`training`), so a PR's workflow can never read their secrets. Optionally add required reviewers (e.g. on
`training`) so a deploy waits for approval. Add these
**to each environment**, with that environment's instance and passwords:

**Secrets**

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Elastic IP or hostname of the instance |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | private key of the deploy key pair |
| `DEPLOY_KNOWN_HOSTS` | the host's SSH key as a `known_hosts` line, so the deploy refuses an impostor host: `ssh-keyscan -t ed25519 <elastic-ip>` (add `-p <port>` if not 22). Check it first: `ssh-keyscan -t ed25519 <elastic-ip> \| ssh-keygen -lf -` must show the same `SHA256:…` as the EC2 console's system log (Actions → Monitor → Get system log) |
| `DEPLOY_PORT` | optional, default 22 |
| `ADMIN_PASSWORD` | Administrator password (used when the site is first created) |
| `DB_ROOT_PASSWORD` | MariaDB root password (long, random; never reused): `openssl rand -base64 32 \| tr -d '\n'` |
| `BACKUP_S3_BUCKET` | backup bucket name (empty = backups stay on the instance) |
| `ALERT_WEBHOOK_URL` | optional Slack/Teams webhook for backup failures |
| `SPICE_AI_ANTHROPIC_API_KEY` | optional Claude key for Ask Data |
| `SPICE_AI_OPENAI_API_KEY` | optional OpenAI key for Ask Data |
| `SPICE_AI_GEMINI_API_KEY` | optional Google Gemini key for Ask Data — from a **billing-enabled (paid)** project: on the free tier Google may use prompts to improve its products |
| `GHCR_PULL_USER`, `GHCR_PULL_TOKEN` | if the GHCR package is private: a user and a PAT with `read:packages` |

Both passwords must be 12+ characters from `A-Z a-z 0-9 . _ ~ + = @ % , : / -` (the deploy job checks this
before touching the host); no secret may contain a quote or newline.

**Variables**

| Variable | dev | training |
|---|---|---|
| `SITE_NAME` (Frappe site name) | `spice-next-facility-dev.labsplatform.com` | `spice-facility-training.labsplatform.com` |
| `DOMAIN` (bare host = HTTPS; `http://host` = plain HTTP) | same as `SITE_NAME` | same as `SITE_NAME` |
| `SITE_URL` (link shown on the deploy) | `https://spice-next-facility-dev.labsplatform.com` | `https://spice-facility-training.labsplatform.com` |
| `DEPLOY_PATH` | `/home/ubuntu/spice_next_facility` |
| `AWS_DEFAULT_REGION` | `ap-south-1` |
| `DATA_VOLUME_ID` | that environment's data volume, e.g. `vol-0123456789abcdef0` (needed until it is mounted) |
| `DATA_DIR` | optional, default `/srv/spice-data` |
| `SPICE_ALLOW_DEMO_SEED` | `1` on dev only: lets a System Manager load demo data over the API (anything else = off) |

The image is pushed with the workflow's own `GITHUB_TOKEN` (`packages: write`) — no registry secret needed
for publishing.

## 4. First deploy

Push to `develop` (dev) or `training` (training), or run the workflow manually on that branch. The deploy job:

1. checks the environment's secrets and that the image was built and attested by this branch,
2. copies `docker-compose.yml`, `Caddyfile`, `deploy.sh` and a freshly written `.env` (CI-owned; do not edit
   it on the host) to `DEPLOY_PATH` over SSH with the pinned host key,
   then runs `prepare-host.sh` (mounts the data volume, installs Docker and swap if missing),
3. runs `deploy.sh`: pull the `sha-<commit>@sha256:…` image → `docker compose up -d` → wait for
   `/api/method/ping` (a first deploy creates the site and installs ERPNext, Health, frappe_theme and
   spice_facility — allow ~10 minutes) → on failure, roll back to the last healthy tag.

Then open the environment's URL, log in as Administrator and complete the **ERPNext setup wizard** (company,
currency, fiscal year). Health's masters (departments, practitioners, service units) come next; demo data
can be loaded with `bench --site <SITE_NAME> execute spice_facility.demo.seed.run --kwargs "{'patients': 50}"`
(non-production only), or, where `SPICE_ALLOW_DEMO_SEED=1`, over the API as a System Manager:
`curl -X POST -H "Authorization: token <key>:<secret>" -d patients=50 https://<host>/api/method/spice_facility.demo.api.seed`
(runs as a background job; the stories plus a synthetic cohort up to 50 patients, re-runnable).

To use Ask Data with Claude, OpenAI or Gemini, open **SPICE AI Settings**, enable the provider, set the model and use
**Test** — keys supplied as secrets above are picked up automatically.

## 5. Every later deploy

Merge to `develop` / `training`. Tests must pass before an image is pushed; `site-setup` runs
`bench migrate` on start (single instance, so there is no migration race).

Run a specific earlier build: re-run the deploy job of that commit's workflow run, or on the host set
`IMAGE_TAG='sha-<commit>@sha256:<digest>'` in `.env` and run `./deploy.sh`. Always include the digest
(from that run's "Push the tested image" step, or `docker buildx imagetools inspect
ghcr.io/medtronic-labs/spice_next_facility:sha-<commit>`, checked with `gh attestation verify
oci://ghcr.io/medtronic-labs/spice_next_facility@sha256:<digest> --repo Medtronic-LABS/spice_next_facility`): a
bare tag can be overwritten, a digest cannot.

## 6. Operations

```bash
cd /home/ubuntu/spice_next_facility
docker compose ps
docker compose logs -f app                                       # supervisord programs
docker exec -it spice-next-facility tail -f /var/log/supervisor/backend.err.log
docker exec -it spice-next-facility supervisorctl status
docker exec -it -u frappe spice-next-facility bash -lc 'cd ~/frappe-bench && bench --site "$SITE_NAME" migrate'
```

**Backups**: daily `bench backup --with-files` into `DATA_DIR/sites/<site>/private/backups`, synced to
`s3://<BACKUP_S3_BUCKET>/<SITE_NAME>/`, local copies kept `BACKUP_RETENTION_DAYS` (7). Restore:

```bash
aws s3 sync s3://<bucket>/<site>/ ./restore/
docker cp ./restore spice-next-facility:/tmp/restore
docker exec -it -u frappe spice-next-facility bash -lc 'cd ~/frappe-bench && \
  bench --site "$SITE_NAME" restore /tmp/restore/<timestamp>-database.sql.gz \
  --with-public-files /tmp/restore/<timestamp>-files.tar --with-private-files /tmp/restore/<timestamp>-private-files.tar \
  --db-root-password "$DB_ROOT_PASSWORD"'
```

Also enable **EBS snapshots of the data volume** (AWS Data Lifecycle Manager, daily) as a second,
whole-volume safety net. To move the data to a new instance: stop the stack, detach the volume, attach it to the
new instance, set `DATA_VOLUME_ID` if it changed and deploy — an existing filesystem is mounted, never formatted.

## Next steps when this outgrows one host

Move MariaDB to Amazon RDS and drop the `mariadb` service. **Not wired up yet:** the image's MariaDB CLI
clients support `DB_TLS=verify` + `DB_SSL_CA`, but the deploy job still writes `DB_TLS='off'`, compose pins
`DB_HOST: mariadb`, no CA bundle is mounted, and Frappe's own connection needs `db_ssl_ca` in the site
config — all four must change before RDS (TLS end to end). Put an ALB + ACM in front
instead of Caddy, and replace SSH deploys with SSM Run Command via GitHub OIDC.
