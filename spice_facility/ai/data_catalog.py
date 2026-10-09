"""What Talk-to-Data may query: an allowlist of datasets with curated fields.

The model only ever picks names from here (as JSON-schema enums); the executor only ever queries these
fields through `frappe.get_list`. `scopes` says which field links a dataset to the page a question is
asked from, so "lab tests per month" on a Patient form means this patient's lab tests.
Field types come from the live meta; fields that do not exist on this site are dropped.
"""

from dataclasses import dataclass, field

import frappe

STANDARD_FIELDS = {"name": "ID", "creation": "Created on", "owner": "Created by"}
DATE_TYPES = ("Date", "Datetime")
NUMBER_TYPES = ("Int", "Float", "Currency", "Percent")


@dataclass(frozen=True)
class Dataset:
	doctype: str
	label: str
	description: str
	date_field: str
	fields: dict  # fieldname -> label
	measures: tuple = ()  # numeric fields (Data-typed vitals are cast when aggregated)
	scopes: dict = field(default_factory=dict)  # page doctype -> field on this dataset
	list_fields: tuple = ()


DATASETS = (
	Dataset("Patient Appointment", "Appointments", "Booked OPD appointments", "appointment_date",
	        {"appointment_date": "Date", "status": "Status", "department": "Department", "practitioner_name": "Practitioner",
	         "appointment_type": "Appointment type", "patient": "Patient", "patient_name": "Patient name"},
	        ("duration",), {"Patient": "patient", "Healthcare Practitioner": "practitioner"},
	        ("appointment_date", "patient_name", "practitioner_name", "status")),
	Dataset("Patient Encounter", "Encounters", "Consultations / visits with a practitioner", "encounter_date",
	        {"encounter_date": "Date", "medical_department": "Department", "practitioner_name": "Practitioner",
	         "appointment_type": "Visit type", "status": "Status", "patient": "Patient", "patient_name": "Patient name"},
	        (), {"Patient": "patient", "Healthcare Practitioner": "practitioner", "Inpatient Record": "inpatient_record"},
	        ("encounter_date", "patient_name", "practitioner_name", "medical_department")),
	Dataset("Lab Test", "Lab tests", "Laboratory tests and their status", "result_date",
	        {"result_date": "Result date", "lab_test_name": "Test", "template": "Test template", "status": "Status",
	         "practitioner_name": "Ordered by", "patient": "Patient", "patient_name": "Patient name"},
	        (), {"Patient": "patient", "Healthcare Practitioner": "practitioner", "Inpatient Record": "inpatient_record",
	             "Emergency Record": "emergency_record"},
	        ("result_date", "lab_test_name", "patient_name", "status")),
	Dataset("Vital Signs", "Vital signs", "Recorded vital signs (BP, pulse, temperature, BMI)", "signs_date",
	        {"signs_date": "Date", "bp_systolic": "Systolic BP", "bp_diastolic": "Diastolic BP", "pulse": "Pulse",
	         "temperature": "Temperature", "respiratory_rate": "Respiratory rate", "bmi": "BMI", "weight": "Weight",
	         "patient": "Patient", "patient_name": "Patient name"},
	        ("bp_systolic", "bp_diastolic", "pulse", "temperature", "respiratory_rate", "bmi", "weight"),
	        {"Patient": "patient", "Inpatient Record": "inpatient_record", "Patient Encounter": "encounter"},
	        ("signs_date", "patient_name", "bp_systolic", "bp_diastolic", "pulse", "temperature")),
	Dataset("Inpatient Record", "Admissions", "Inpatient admissions", "admitted_datetime",
	        {"admitted_datetime": "Admitted on", "status": "Status", "medical_department": "Department",
	         "primary_practitioner": "Primary practitioner", "admission_service_unit_type": "Ward type",
	         "patient": "Patient", "patient_name": "Patient name", "discharge_datetime": "Discharged on"},
	        ("expected_length_of_stay",), {"Patient": "patient", "Healthcare Practitioner": "primary_practitioner"},
	        ("admitted_datetime", "patient_name", "status", "medical_department")),
	Dataset("Emergency Record", "Emergency visits", "Emergency department visits and triage", "arrival_datetime",
	        {"arrival_datetime": "Arrival", "status": "Status", "triage_level": "Triage level", "arrival_mode": "Arrival mode",
	         "disposition": "Disposition", "medical_department": "Department", "patient": "Patient", "patient_name": "Patient name"},
	        (), {"Patient": "patient"}, ("arrival_datetime", "patient_name", "triage_level", "status")),
	Dataset("Therapy Session", "Therapy sessions", "Physiotherapy / rehabilitation sessions", "start_date",
	        {"start_date": "Date", "therapy_type": "Therapy", "practitioner": "Therapist", "patient": "Patient",
	         "patient_name": "Patient name"},
	        ("total_counts_targeted", "total_counts_completed"),
	        {"Patient": "patient", "Therapy Plan": "therapy_plan", "Healthcare Practitioner": "practitioner"},
	        ("start_date", "patient_name", "therapy_type")),
	Dataset("Clinical Procedure", "Procedures", "Clinical procedures such as ECG, dressing, nebulisation", "start_date",
	        {"start_date": "Date", "procedure_template": "Procedure", "status": "Status", "medical_department": "Department",
	         "practitioner": "Practitioner", "patient": "Patient", "patient_name": "Patient name"},
	        (), {"Patient": "patient", "Healthcare Practitioner": "practitioner", "Inpatient Record": "inpatient_record"},
	        ("start_date", "procedure_template", "patient_name", "status")),
	Dataset("Medication Request", "Medication orders", "Prescribed medicines", "order_date",
	        {"order_date": "Ordered on", "medication": "Medication", "status": "Status", "practitioner_name": "Prescriber",
	         "patient": "Patient", "patient_name": "Patient name"},
	        ("quantity",), {"Patient": "patient", "Healthcare Practitioner": "practitioner", "Patient Encounter": "order_group",
	                        "Inpatient Record": "inpatient_record"},
	        ("order_date", "medication", "patient_name", "status")),
	Dataset("Service Request", "Orders", "Lab, imaging, procedure and therapy orders", "order_date",
	        {"order_date": "Ordered on", "template_dn": "Order", "template_dt": "Order type", "status": "Status",
	         "practitioner_name": "Ordered by", "patient": "Patient", "patient_name": "Patient name"},
	        (), {"Patient": "patient", "Healthcare Practitioner": "practitioner", "Patient Encounter": "order_group",
	             "Inpatient Record": "inpatient_record"},
	        ("order_date", "template_dn", "patient_name", "status")),
	Dataset("Sales Invoice", "Invoices", "Patient bills / invoices", "posting_date",
	        {"posting_date": "Date", "status": "Status", "customer": "Customer", "patient": "Patient",
	         "patient_name": "Patient name", "company": "Company"},
	        ("grand_total", "outstanding_amount"), {"Patient": "patient"},
	        ("posting_date", "customer", "grand_total", "status")),
	Dataset("Insurance Claim", "Insurance claims", "Claims submitted to insurance payors", "posting_date",
	        {"posting_date": "Date", "status": "Status", "insurance_payor": "Payor", "patient": "Patient"},
	        ("insurance_claim_amount", "approved_amount", "paid_amount", "outstanding_amount"),
	        {"Patient": "patient", "Patient Insurance Policy": "insurance_policy"},
	        ("posting_date", "insurance_payor", "insurance_claim_amount", "status")),
	Dataset("Patient", "Patients", "Registered patients and demographics", "creation",
	        {"creation": "Registered on", "sex": "Sex", "blood_group": "Blood group", "status": "Status",
	         "marital_status": "Marital status", "occupation": "Occupation", "patient_name": "Patient name"},
	        (), {}, ("patient_name", "sex", "blood_group", "status")),
	Dataset("Healthcare Practitioner", "Practitioners", "Doctors and therapists", "creation",
	        {"practitioner_name": "Name", "department": "Department", "status": "Status", "practitioner_type": "Type",
	         "gender": "Gender"},
	        ("op_consulting_charge",), {}, ("practitioner_name", "department", "status")),
	Dataset("Healthcare Service Unit", "Rooms & beds", "Wards, rooms and beds with occupancy", "creation",
	        {"healthcare_service_unit_name": "Unit", "service_unit_type": "Unit type", "occupancy_status": "Occupancy",
	         "is_group": "Is group", "parent_healthcare_service_unit": "Parent unit"},
	        (), {}, ("healthcare_service_unit_name", "service_unit_type", "occupancy_status")),
)

BY_DOCTYPE = {d.doctype: d for d in DATASETS}


def field_type(doctype, fieldname):
	if fieldname in STANDARD_FIELDS:
		return "Datetime" if fieldname == "creation" else "Data"
	df = frappe.get_meta(doctype).get_field(fieldname)
	return df.fieldtype if df else None


def field_options(doctype, fieldname):
	df = frappe.get_meta(doctype).get_field(fieldname)
	return df.options if df else None


def resolved(dataset: Dataset) -> dict:
	"""The dataset as this site actually has it: only existing fields, with their types."""
	fields = {f: label for f, label in dataset.fields.items() if field_type(dataset.doctype, f)}
	measures = tuple(m for m in dataset.measures if field_type(dataset.doctype, m))
	return {
		"doctype": dataset.doctype, "label": dataset.label, "description": dataset.description,
		"date_field": dataset.date_field if field_type(dataset.doctype, dataset.date_field) in DATE_TYPES else None,
		"fields": {f: {"label": label, "type": field_type(dataset.doctype, f),
		               "options": field_options(dataset.doctype, f)} for f, label in fields.items()},
		"measures": {m: dataset.fields.get(m) or frappe.get_meta(dataset.doctype).get_label(m) for m in measures},
		"scopes": {page: f for page, f in dataset.scopes.items() if field_type(dataset.doctype, f)},
		"list_fields": [f for f in dataset.list_fields if f in fields],
	}


def available(user=None):
	"""Datasets the user can read, resolved for this site."""
	return [resolved(d) for d in DATASETS
	        if frappe.db.exists("DocType", d.doctype) and frappe.has_permission(d.doctype, "read", user=user)]


def get(doctype, user=None):
	dataset = BY_DOCTYPE.get(doctype)
	if not dataset or not frappe.has_permission(doctype, "read", user=user):
		return None
	return resolved(dataset)


def catalog_text(datasets):
	return "\n".join(f"- {d['doctype']}: {d['description']}" for d in datasets)


def fields_text(dataset):
	lines = [f"- {f}: {meta['type']} — {meta['label']}" for f, meta in dataset["fields"].items()]
	lines += [f"- {m}: number — {label} (measure)" for m, label in dataset["measures"].items() if m not in dataset["fields"]]
	if dataset["date_field"]:
		lines.append(f"(default date field: {dataset['date_field']})")
	return "\n".join(lines)
