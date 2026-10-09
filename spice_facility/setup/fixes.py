"""Corrections to Frappe Health v16 site data. Each is idempotent and safe to re-run."""

import json

import frappe


def apply_fixes():
	fix_lab_test_history_fieldtype()


def fix_lab_test_history_fieldtype():
	"""Health ships the Lab Test patient-history config with `lab_test_comment` typed as Table. It is
	a Text field, so building the history entry looks up a child doctype named None and submitting
	any Lab Test that has a comment fails with "DocType None not found"."""
	row = frappe.db.get_value(
		"Patient History Standard Document Type",
		{"document_type": "Lab Test"},
		["name", "selected_fields"],
		as_dict=True,
	)
	if not row or not row.selected_fields:
		return

	fields = json.loads(row.selected_fields)
	actual = frappe.get_meta("Lab Test").get_field("lab_test_comment")
	changed = False
	for field in fields:
		if field.get("fieldname") == "lab_test_comment" and actual and field.get("fieldtype") != actual.fieldtype:
			field["fieldtype"] = actual.fieldtype
			changed = True
	if changed:
		frappe.db.set_value("Patient History Standard Document Type", row.name, "selected_fields", json.dumps(fields))
