"""Load the demo data over the API (e.g. on a freshly deployed demo/dev site, without shell access).

    POST /api/method/spice_facility.demo.api.seed   {"patients": 50}

System Manager only, and only where the deployment opts in with SPICE_ALLOW_DEMO_SEED=1 (environment or
site_config `spice_allow_demo_seed`), so it can never run on a site holding real patients. The seed runs as a
background job; the response is the job id. Progress and failures are in the worker log.
"""

import os

import frappe
from frappe import _

FLAG = "SPICE_ALLOW_DEMO_SEED"
JOB = "spice_facility.demo.seed.run"
MAX_PATIENTS = 500


def demo_seed_allowed() -> bool:
	value = frappe.conf.get(FLAG.lower())
	if value is None:
		value = os.environ.get(FLAG)
	return str(value).strip().lower() in ("1", "true", "yes", "on")


@frappe.whitelist(methods=["POST"])
def seed(patients: int = 50, company: str | None = None) -> dict:
	frappe.only_for("System Manager")
	if not demo_seed_allowed():
		frappe.throw(_("Demo data is not enabled on this site."), frappe.PermissionError)
	patients = int(patients)
	if not 1 <= patients <= MAX_PATIENTS:
		frappe.throw(_("Patients must be between 1 and {0}.").format(MAX_PATIENTS))
	job_id = "spice-demo-seed"  # enqueue namespaces it by site
	job = frappe.enqueue(JOB, queue="long", timeout=3600, job_id=job_id, deduplicate=True,
	                     company=company, patients=patients)
	return {"queued": job is not None, "job_id": job_id, "patients": patients}
