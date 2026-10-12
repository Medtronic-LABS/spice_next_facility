"""Prepare a test site for the Ask Data end-to-end suite (e2e/): fixtures, chat switched on, the fake model.

	bench --site <site> set-config allow_tests true
	bench --site <site> set-config spice_ai_fake_provider 1
	bench --site <site> execute spice_facility.tests.e2e_setup.prepare

Refuses to run unless the fake model is allowed, so it can never touch a real site's AI settings.
"""

import frappe

from spice_facility.ai.config import SETTINGS, fake_provider_allowed
from spice_facility.tests.test_ai import ensure_fixtures

# A test site asks far more questions an hour than a person; reruns must not hit the per-user AI request limit.
CHAT_SETTINGS = {"chat_enabled": 1, "floating_chat_enabled": 1, "rate_limit_per_hour": 1000}


def prepare():
	if not fake_provider_allowed():
		frappe.throw("Set allow_tests and spice_ai_fake_provider on this site first: the e2e suite needs the fake model.")
	_skip_setup_wizard()
	ensure_fixtures()
	for field, value in CHAT_SETTINGS.items():
		frappe.db.set_single_value(SETTINGS, field, value)
	frappe.db.commit()
	frappe.clear_cache()
	print("e2e site ready")


def _skip_setup_wizard():
	"""A fresh CI site has never run the setup wizard, and the desk sends every page there until it has. Ask Data
	needs no company or fiscal year, so a test site is simply marked as set up (frappe.is_setup_complete)."""
	if frappe.is_setup_complete():
		return
	for app in ("frappe", "erpnext"):
		frappe.db.set_value("Installed Application", {"app_name": app}, "is_setup_complete", 1)
	frappe.db.set_single_value("System Settings", "setup_complete", 1)
