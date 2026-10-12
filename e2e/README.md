# Ask Data end-to-end tests

Playwright tests for the Ask Data chat, run against a real site where the **fake model**
(`spice_facility/ai/providers/fake.py`) answers. No AI provider, key or network is needed.

The fake model exists only on a site that allows tests **and** sets `spice_ai_fake_provider`. Never set
either on a real site.

## Against the local stack (`deploy/local-test.sh up`)

```bash
docker exec spice-next-facility-local bash -c 'cd /home/frappe/frappe-bench && \
  bench --site spice-facility-local.localhost set-config allow_tests true && \
  bench --site spice-facility-local.localhost set-config spice_ai_fake_provider 1 && \
  bench --site spice-facility-local.localhost execute spice_facility.tests.e2e_setup.prepare'
cd e2e && npm ci && npx playwright install chromium
E2E_BASE_URL=http://localhost:8088 E2E_PASSWORD=admin npx playwright test
```

## In CI

`.github/workflows/docker-publish.yml` runs the suite against the image it just built. It runs when Ask Data
files change, or on a manual run.
