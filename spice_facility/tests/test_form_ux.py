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
	native_layout_problems,
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

	def test_patient_native_layout_matches_this_site(self):
		# a Health upgrade that adds or renames a Patient field shows up here, not as a broken form
		self.assertEqual(native_layout_problems(), [])

	def test_patient_tabs_and_sections_read_in_order(self):
		meta = frappe.get_meta("Patient")
		visible_tabs = [f.label for f in meta.fields if f.fieldtype == "Tab Break" and not f.hidden]
		self.assertEqual(visible_tabs, ["Overview", "Details", "Medical history", "Records"])
		order = [f.fieldname for f in meta.fields]
		# contact, then preferences and consent (with the cloud AI opt-out), all in Details
		self.assertLess(order.index("sf_sec_contact"), order.index("mobile"))
		self.assertLess(order.index("address_html"), order.index("sf_sec_preferences"))
		self.assertEqual(order[order.index("report_preference") + 1], "spice_no_cloud_ai")
		self.assertLess(order.index("spice_no_cloud_ai"), order.index("medical_history_tab"))
		self.assertEqual(meta.get_field("dashboard_tab").label, "Records")

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

	def test_patient_overview_blocks(self):
		from spice_facility.tests.test_ai import TEST_PATIENT, ensure_fixtures

		ensure_fixtures()  # a patient with two submitted encounters
		frappe.db.set_value("Patient", TEST_PATIENT, {"tobacco_current_use": "5/day", "medical_history": "T2DM since 2018"})
		glance = get_block("patient_glance", "Patient", TEST_PATIENT)
		self.assertIn({"label": "Tobacco", "value": "5/day"}, glance["risks"])
		self.assertEqual(glance["history"], "T2DM since 2018")
		history = get_block("patient_history", "Patient", TEST_PATIENT)
		self.assertEqual({e["name"] for e in history["events"]} >= {"SF-TEST-ENC-1", "SF-TEST-ENC-2"}, True)
		self.assertEqual(history["events"], sorted(history["events"], key=lambda e: str(e["date"] or ""), reverse=True))
		upcoming = get_block("patient_upcoming", "Patient", TEST_PATIENT)
		self.assertTrue(all(a.status not in ("Cancelled", "No Show") for a in upcoming["appointments"]))
		self.assertIn("results", get_block("patient_results", "Patient", TEST_PATIENT))
		self.assertEqual(get_block("patient_header", "Patient", TEST_PATIENT)["patient"], TEST_PATIENT)

	def test_out_of_range_lines_are_short(self):
		from spice_facility.api.form_summary import _short_flag

		self.assertEqual(_short_flag("HbA1c 8.2 % (high; normal <5.7 normal) — HbA1c, 2026-10-09"), "HbA1c 8.2 % high")
		self.assertEqual(_short_flag("HDL 38 mg/dL (low; normal >40) — Lipid Profile, 2026-10-09"), "HDL 38 mg/dL low")

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
