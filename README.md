## SPICE Facility

Facility (hospital) layer for SPICE on top of **Frappe Health** (`healthcare`, github.com/frappe/health), ERPNext and frappe_theme, Frappe v16.

### What it does

| Area | Where | When |
|---|---|---|
| Desk navigation — Healthcare **Dock → Menu → items** (9 menus) | `desk_navigation/` | install + every `bench migrate` |
| Full Width desk layout as the default for every user, with the page header and main layout freed of Frappe 16's 2100px cap | `public/js/full_width_default.js`, `public/css/full_width.css` | every desk load |
| Health v16 fix: Lab Test history config types `lab_test_comment` as Table (it is Text), which crashes submitting a commented Lab Test | `setup/fixes.py` | install + every `bench migrate` |
| Demo data across every Health module | `demo/seed.py` | only when run explicitly |
| **Overview tab** on 13 Health forms — related-record tables, number cards, charts and clinical summaries via frappe_theme `sva_ft` | `form_ux/` (spec → apply), `api/form_summary.py`, `public/js/form_ux*.js`, `public/css/form_ux.css` | install + every `bench migrate` |
| Missing / broken Connections (e.g. Patient → Emergency Record, Practitioner → Inpatient Record on `primary_practitioner`) | `form_ux/connections.py` | install + every `bench migrate` |
| **AI summary** of the open record (Patient, Encounter, Admission, Emergency, Therapy Plan) on the local model | `ai/summary_context.py`, `ai/clinical_flags.py`, `api/ai_summary.py`, `public/js/ai_summary.js` | on demand, cached |
| **Ask Data** — talk to data with Ollama, Claude or OpenAI; text + chart + table, page-aware floating chat | `ai/{data_catalog,query,talk_to_data}.py`, `api/talk_to_data.py`, page `ask-data`, `public/js/ask_data/` | on demand |
| Read-only **MCP server** over the same governed queries | `api/mcp.py` | on demand |
| frappe_theme fix: form-scoped Dashboard Charts break on the `{}` filter frappe_theme always sends | `overrides/frappe_theme.py` (`override_whitelisted_methods`) | always |

#### Navigation model (Frappe 16.5x)

Frappe 16 resolves which menu a workspace or document opens in **by module**. Health ships everything
in one module, so this app owns one module per menu (`Outpatient`, `Emergency`, `Inpatient`,
`Diagnostics`, `Pharmacy`, `Rehabilitation`, `Billing and Insurance`, `Healthcare Setup`), moves each
area's dashboard workspace into it, and marks the menu that owns each Health doctype
(`is_default_module`). The menus are site-layer `Sidebar` rows (`standard=0`) and the site layer of the
`healthcare` `Dock`, so nothing in the Health app is edited. Uninstalling moves the dashboards back to
the `Healthcare` module first.

#### Overview tab (frappe_theme `sva_ft`)

Each form in `form_ux/spec.py` gets a first **Overview** tab built from four block kinds, each an HTML
custom field (`sf_*`) carrying an `sva_ft` Property Setter:

| Kind | Renders | Notes |
|---|---|---|
| `table` | connected-doctype table (Direct / Indirect / Referenced) | always `redirect_to_main_form`, which also keeps the linked doctype's own form editable |
| `card` | Number Card (Count) | frappe_theme scopes it to the open record; apply refuses a source doctype it cannot scope cleanly |
| `chart` | Dashboard Chart (Count time series) | Group By is not offered — frappe_theme splices the filter object into the query |
| `html` | Custom HTML Block → `spice_facility.form_ux.mount()` | data from `api/form_summary.get_block` (permission-checked, capped) |

Forms covered: Patient, Patient Encounter, Inpatient Record, Healthcare Practitioner, Emergency Record,
Lab Test, Observation, Diagnostic Report, Sample Collection, Therapy Plan, Patient Insurance Policy,
Insurance Claim, Healthcare Service Unit. A `field_order` Property Setter keeps Overview first, and forms
without a dashboard tab get a trailing **Connections** tab. Health v16's Insurance Claim declares two
fields twice, so custom-field validation is skipped for it only while that defect exists.

#### AI (SPICE AI Settings)

- **Providers:** Ollama (local, default `http://ollama:11434`, `llama3.1:8b`), Claude and OpenAI (or any
  OpenAI-compatible endpoint). Keys are encrypted Password fields, never sent to the browser; site_config /
  env keys `spice_ai_ollama_url`, `spice_ai_anthropic_api_key`, `spice_ai_openai_api_key` override them.
  Each provider has **Test** / **Refresh models** buttons. Access is limited to **Allowed roles**.
- **Data policy:** cloud models receive only the question, dataset/field names and earlier query plans.
  Records, results and page context go only to the local model, which writes every answer and summary.
- **AI summary:** abnormal labs/vitals are flagged in code (`clinical_flags.py`) from each record's own
  normal ranges; a `must_mention` checklist keeps the small model on the important facts. Summaries are
  cached in `SPICE AI Summary` and marked stale when the record's data changes. Nothing is written into
  the clinical record.
- **Ask Data:** the model fills a QuerySpec whose every name is an enum from `data_catalog.py`; it is
  validated, run with `frappe.get_list` as the user (their permissions apply), aggregated in Python and
  narrated locally. On a form, questions are scoped to that record; record questions ("last HbA1c?") are
  answered from the record by the local model. Answers link only to records they are grounded on; bars
  drill down to filtered lists; charts can be pinned (frappe_theme `create_dashboard_chart`). Every
  question is logged in `SPICE AI Query Log`.
- **MCP:** `POST /api/method/spice_facility.api.mcp.mcp` (Frappe API key auth) — `list_datasets`,
  `describe_dataset`, `run_query`, `ask`; read-only.
- **Needs an RQ worker** (`bench worker --queue default,short,long`); a dedicated `--queue long` worker
  keeps AI jobs from waiting behind other sites' backlog.

### Deploy (AWS EC2)

`develop` → https://spice-facility-dev.labsplatform.com, `training` → https://spice-facility-training.labsplatform.com
(one EC2 each); `main` and `v*` tags build, test and publish only. `.github/workflows/docker-publish.yml` builds
a thin app image on a **reusable base** (`docker/base/`: Frappe, ERPNext, Frappe Health and frappe_theme pinned
in `docker/base/versions.env`, rebuilt only when that file changes), runs the test suite inside it against
MariaDB, pushes to `ghcr.io/medtronic-labs/spice_next_facility`, and deploys over SSH (`deploy/`: Caddy for HTTPS +
the app + MariaDB, health check with automatic rollback, daily backups to S3). Runbook, AWS resources and the
secrets list: [`docs/deployment/aws-ec2.md`](docs/deployment/aws-ec2.md). Try the same stack locally first with
`deploy/local-test.sh up` (http://localhost:8088).

### Install

```bash
bench get-app <this repo>
bench --site <site> install-app spice_facility
```

### Demo data

```bash
bench --site <site> execute spice_facility.demo.seed.run                     # default company
bench --site <site> execute spice_facility.demo.seed.run --kwargs "{'company': 'MDT'}"
```

Re-runnable: masters are get-or-create and each patient story is seeded once.

### License

gpl-3.0
