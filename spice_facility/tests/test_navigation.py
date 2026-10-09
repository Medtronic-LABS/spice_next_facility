import json

import frappe
from frappe.tests import IntegrationTestCase

from spice_facility.desk_navigation.apply import (
	apply_navigation,
	doctype_owners,
	menu_module,
	missing_targets,
	site_dock_name,
)
from spice_facility.desk_navigation.menus import HOST_MODULE, MENUS, WORKSPACE_MENU
from spice_facility.setup.fixes import fix_lab_test_history_fieldtype


class TestDeskNavigation(IntegrationTestCase):
	def setUp(self):
		apply_navigation()

	def test_every_menu_link_resolves(self):
		self.assertEqual(missing_targets(), [])

	def test_each_menu_is_a_sidebar_in_its_own_module(self):
		for title in MENUS:
			self.assertEqual(frappe.db.get_value("Sidebar", title, "module"), menu_module(title))

	def test_dock_lists_every_menu_in_order(self):
		items = frappe.get_all("Dock Item", {"parent": site_dock_name()}, pluck="link_to", order_by="idx")
		self.assertEqual(items, list(MENUS))

	def test_area_dashboards_open_in_their_menu(self):
		for workspace, menu in WORKSPACE_MENU.items():
			self.assertEqual(frappe.db.get_value("Workspace", workspace, "module"), menu)

	def test_each_health_doctype_has_one_owning_menu(self):
		owners = doctype_owners()
		for doctype, menu in owners.items():
			claimed = frappe.get_all(
				"Sidebar Item", {"link_to": doctype, "is_default_module": 1, "parenttype": "Sidebar"}, pluck="parent"
			)
			self.assertEqual(claimed, [menu], doctype)
		self.assertNotIn(HOST_MODULE, owners.values())

	def test_reapplying_is_idempotent(self):
		before = frappe.db.count("Sidebar Item", {"parent": ["in", list(MENUS)]})
		apply_navigation()
		self.assertEqual(frappe.db.count("Sidebar Item", {"parent": ["in", list(MENUS)]}), before)


class TestHealthFixes(IntegrationTestCase):
	def test_lab_test_comment_history_field_matches_its_doctype(self):
		name = frappe.db.get_value("Patient History Standard Document Type", {"document_type": "Lab Test"})
		if not name:
			self.skipTest("Health did not ship a Lab Test history config")
		fields = json.loads(frappe.db.get_value("Patient History Standard Document Type", name, "selected_fields"))
		for field in fields:
			if field["fieldname"] == "lab_test_comment":
				field["fieldtype"] = "Table"
		frappe.db.set_value("Patient History Standard Document Type", name, "selected_fields", json.dumps(fields))

		fix_lab_test_history_fieldtype()

		fields = json.loads(frappe.db.get_value("Patient History Standard Document Type", name, "selected_fields"))
		comment = next(f for f in fields if f["fieldname"] == "lab_test_comment")
		self.assertEqual(comment["fieldtype"], frappe.get_meta("Lab Test").get_field("lab_test_comment").fieldtype)
