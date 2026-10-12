"""Prepare a test site for the Ask Data end-to-end suite (e2e/): fixtures, chat switched on, the fake model.

	bench --site <site> set-config allow_tests true
	bench --site <site> set-config spice_ai_fake_provider 1
	bench --site <site> execute spice_facility.tests.e2e_setup.prepare

Refuses to run unless the fake model is allowed, so it can never touch a real site's AI settings.
"""

import frappe

from spice_facility.ai.config import SETTINGS, fake_provider_allowed
from spice_facility.tests.test_ai import ensure_fixtures

CHAT_SETTINGS = {"chat_enabled": 1, "floating_chat_enabled": 1}


def prepare():
	if not fake_provider_allowed():
		frappe.throw("Set allow_tests and spice_ai_fake_provider on this site first: the e2e suite needs the fake model.")
	ensure_fixtures()
	for field, value in CHAT_SETTINGS.items():
		frappe.db.set_single_value(SETTINGS, field, value)
	frappe.db.commit()
	frappe.clear_cache()
	print("e2e site ready")
