"""Connections-tab fixes for Health doctypes: add the reverse links Health leaves out, and hide the
ones that point at a field that does not exist. Written the way Customize Form writes them: custom
`DocType Link` rows, and `hidden` Property Setters on standard rows."""

import frappe

# (doctype, linked doctype, link fieldname, group)
ADD = [
	("Patient", "Emergency Record", "patient", "Emergency"),
	("Patient", "Diagnostic Report", "patient", "Diagnostics"),
	("Patient", "Patient Allergy", "patient", "Appointments and Encounters"),
	("Patient", "Treatment Counselling", "patient", "Admissions"),
	("Patient", "Insurance Claim", "patient", "Insurance"),
	("Inpatient Record", "Patient Encounter", "inpatient_record", "Clinical"),
	("Inpatient Record", "Vital Signs", "inpatient_record", "Clinical"),
	("Inpatient Record", "Lab Test", "inpatient_record", "Clinical"),
	("Inpatient Record", "Inpatient Medication Order", "inpatient_record", "Medication"),
	("Healthcare Practitioner", "Inpatient Record", "primary_practitioner", "Admissions"),
	("Emergency Record", "Lab Test", "emergency_record", "Diagnostics"),
	("Emergency Record", "Inpatient Record", "emergency_record", "Admission"),
	("Patient Insurance Policy", "Insurance Claim", "insurance_policy", "Transactions"),
	("Patient Insurance Policy", "Patient Appointment", "insurance_policy", "Transactions"),
]

# (doctype, linked doctype, link fieldname) shipped by Health with a fieldname the linked doctype lacks
HIDE = [("Healthcare Practitioner", "Inpatient Record", "practitioner")]


def connection_problems():
	problems = [f"{dt} -> {link}.{field}: field missing" for dt, link, field, _group in ADD
	            if not frappe.get_meta(link).get_field(field)]
	problems += [f"{dt} -> {link}.{field}: field exists now; stop hiding it" for dt, link, field in HIDE
	             if frappe.get_meta(link).get_field(field)]
	return problems


def apply_connections():
	for doctype, link_doctype, fieldname, group in ADD:
		if frappe.db.exists("DocType Link", {"parent": doctype, "link_doctype": link_doctype, "link_fieldname": fieldname}):
			continue
		frappe.get_doc({
			"doctype": "DocType Link", "parent": doctype, "parenttype": "DocType", "parentfield": "links",
			"link_doctype": link_doctype, "link_fieldname": fieldname, "group": group, "custom": 1,
		}).insert(ignore_permissions=True)
	for doctype, link_doctype, fieldname in HIDE:
		row = frappe.db.get_value("DocType Link", {"parent": doctype, "link_doctype": link_doctype,
		                                           "link_fieldname": fieldname, "custom": 0})
		if row and not frappe.db.exists("Property Setter", {"doc_type": doctype, "row_name": row, "property": "hidden"}):
			frappe.get_doc({
				"doctype": "Property Setter", "doctype_or_field": "DocType Link", "doc_type": doctype,
				"row_name": row, "property": "hidden", "value": "1", "property_type": "Check",
				"is_system_generated": 1,
			}).insert(ignore_permissions=True)
	for doctype in {d for d, *_ in ADD} | {d for d, *_ in HIDE}:
		frappe.clear_cache(doctype=doctype)


def remove_connections():
	for doctype, link_doctype, fieldname, _group in ADD:
		for name in frappe.get_all("DocType Link", {"parent": doctype, "link_doctype": link_doctype,
		                                            "link_fieldname": fieldname, "custom": 1}, pluck="name"):
			frappe.db.delete("DocType Link", name)
	for doctype, link_doctype, fieldname in HIDE:
		row = frappe.db.get_value("DocType Link", {"parent": doctype, "link_doctype": link_doctype, "link_fieldname": fieldname})
		if row:
			frappe.db.delete("Property Setter", {"doc_type": doctype, "row_name": row, "property": "hidden"})
		frappe.clear_cache(doctype=doctype)
