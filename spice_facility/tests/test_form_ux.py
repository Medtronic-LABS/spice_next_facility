import json

import frappe
from frappe.tests import IntegrationTestCase

from spice_facility.api.form_summary import BUILDERS, ROW_CAP, get_block
from spice_facility.form_ux.apply import (
	CONNECTIONS_TAB,
	OVERVIEW_TAB,
	PREFIX,
	SVA_FT,
	apply_form_ux,
	iter_blocks,
	scoping_field,
	spec_problems,
)
from spice_facility.form_ux.connections import connection_problems
from spice_facility.form_ux.spec import FORMS
from spice_facility.overrides.frappe_theme import get_chart_data

NESTED_JSON_KEYS = ("crud_permissions", "listview_settings", "extended_condition")


def sf_state(doctype):
	fields = frappe.get_all("Custom Field", {"dt": doctype, "fieldname": ["like", f"{PREFIX}%"]},
	                        ["fieldname", "modified"], order_by="fieldname")
	setters = frappe.get_all("Property Setter", {"doc_type": doctype, "property": SVA_FT}, ["name", "value"],
	                         order_by="name")
	return fields, setters


class TestFormUxSpec(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_form_ux()

	def test_spec_and_connections_resolve_on_this_site(self):
		self.assertEqual(spec_problems() + connection_problems(), [])

	def test_overview_tab_comes_first(self):
		for doctype in FORMS:
			self.assertEqual(frappe.get_meta(doctype).fields[0].fieldname, OVERVIEW_TAB, doctype)

	def test_connections_panel_has_a_tab(self):
		for doctype in FORMS:
			tabs = [f for f in frappe.get_meta(doctype).fields if f.fieldtype == "Tab Break" and f.show_dashboard]
			self.assertTrue(tabs, doctype)
			self.assertNotEqual(tabs[0].fieldname, OVERVIEW_TAB, doctype)

	def test_every_block_has_a_valid_compact_sva_ft(self):
		for doctype, blocks in FORMS.items():
			for block in iter_blocks(blocks):
				value = frappe.db.get_value("Property Setter", f"{doctype}-{block['fieldname']}-{SVA_FT}", "value")
				self.assertTrue(value, f"{doctype}.{block['fieldname']}")
				self.assertNotIn('": ', value, "frappe_theme matches compact JSON only")
				conf = json.loads(value)
				for key in NESTED_JSON_KEYS:
					if key in conf:
						self.assertIsInstance(conf[key], str, f"{key} must be a JSON string")
						json.loads(conf[key])
				if block["kind"] == "table":
					self.assertEqual(conf["redirect_to_main_form"], 1, "keeps the linked doctype's form editable")

	def test_cards_and_charts_scope_to_the_open_document(self):
		for doctype, blocks in FORMS.items():
			for block in iter_blocks(blocks):
				if block["kind"] in ("card", "chart"):
					self.assertEqual(scoping_field(block["document_type"], doctype), block["scope"], block["name"])

	def test_reapplying_changes_nothing(self):
		before = {doctype: sf_state(doctype) for doctype in FORMS}
		apply_form_ux()
		after = {doctype: sf_state(doctype) for doctype in FORMS}
		self.assertEqual(before, after)


class TestFormSummaryApi(IntegrationTestCase):
	def test_unknown_block_and_wrong_doctype_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			get_block("nope", "Patient", "x")
		with self.assertRaises(frappe.ValidationError):
			get_block("patient_header", "Lab Test", "x")

	def test_reading_requires_permission_on_the_document(self):
		patient = frappe.db.get_value("Patient", {})
		if not patient:
			self.skipTest("no Patient on this site")
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				get_block("patient_header", "Patient", patient)
		finally:
			frappe.set_user("Administrator")

	def test_every_block_answers_for_a_real_record(self):
		for key, (doctypes, _builder) in BUILDERS.items():
			doctype = doctypes[0]
			name = frappe.db.get_value(doctype, {})
			if not name:
				continue
			data = get_block(key, doctype, name)
			self.assertIsInstance(data, dict, key)
			for value in data.values():
				if isinstance(value, list):
					self.assertLessEqual(len(value), max(ROW_CAP, 200), f"{key} must cap its rows")


class TestChartOverride(IntegrationTestCase):
	def test_empty_filters_are_dropped_before_frappe_theme(self):
		chart = frappe.db.get_value("Dashboard Chart", {"name": ["like", "SF %"]})
		patient = frappe.db.get_value("Patient", {})
		if not (chart and patient):
			self.skipTest("needs an SF chart and a Patient")
		details = json.dumps(frappe.get_doc("Dashboard Chart", chart).as_dict(), default=str)
		result = get_chart_data("Document Type", details, None, "Patient", patient, "{}")
		self.assertNotIn("Expected 'and' or 'or'", result.get("message") or "")
