## SPICE Facility

Facility (hospital) layer for SPICE on top of **Frappe Health** (`healthcare`, github.com/frappe/health) and ERPNext, Frappe v16.

### What it does

| Area | Where | When |
|---|---|---|
| Desk navigation — Healthcare **Dock → Menu → items** (9 menus) | `desk_navigation/` | install + every `bench migrate` |
| Full Width desk layout as the default for every user | `public/js/full_width_default.js` | every desk load |
| Health v16 fix: Lab Test history config types `lab_test_comment` as Table (it is Text), which crashes submitting a commented Lab Test | `setup/fixes.py` | install + every `bench migrate` |
| Demo data across every Health module | `demo/seed.py` | only when run explicitly |

#### Navigation model (Frappe 16.5x)

Frappe 16 resolves which menu a workspace or document opens in **by module**. Health ships everything
in one module, so this app owns one module per menu (`Outpatient`, `Emergency`, `Inpatient`,
`Diagnostics`, `Pharmacy`, `Rehabilitation`, `Billing and Insurance`, `Healthcare Setup`), moves each
area's dashboard workspace into it, and marks the menu that owns each Health doctype
(`is_default_module`). The menus are site-layer `Sidebar` rows (`standard=0`) and the site layer of the
`healthcare` `Dock`, so nothing in the Health app is edited. Uninstalling moves the dashboards back to
the `Healthcare` module first.

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
