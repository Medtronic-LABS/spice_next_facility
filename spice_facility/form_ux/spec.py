"""The Overview tab added to each Frappe Health form, as data.

Every block becomes one HTML custom field rendered by frappe_theme from its `sva_ft` property:
  - `table`  -> "DocType (Direct|Indirect|Referenced)" connected table, always opening the full form
  - `card`   -> "Number Card" (Count only, auto-scoped to the open document by frappe_theme)
  - `chart`  -> "Dashboard Chart" (auto-scoped the same way)
  - `html`   -> "Custom HTML Block" mounted by public/js/form_ux.js with data from api/form_summary.py

frappe_theme scopes cards and charts to the first Link field pointing at the form's doctype, and ANDs
reference filters when the source doctype has a Link-to-DocType + Dynamic Link pair, which zeroes the
count. So each card/chart names the field it expects to be scoped by (`scope`), and apply.py refuses a
source that would not scope that way.
"""

TODAY = "today"  # frappe_theme substitutes the current date for this literal in extended conditions


def col(fieldname, label, fieldtype="Data", width=2):
	return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}


def table(fieldname, title, link_doctype, link_fieldname, columns, crud=("read",), conditions=None, connection="Direct", **extra):
	return {
		"kind": "table", "fieldname": fieldname, "label": title, "link_doctype": link_doctype,
		"link_fieldname": link_fieldname, "columns": columns, "crud": list(crud), "conditions": conditions or [],
		"connection": connection, "extra": extra,
	}


def card(fieldname, name, label, document_type, scope, filters=None):
	return {"kind": "card", "fieldname": fieldname, "name": name, "label": label,
	        "document_type": document_type, "scope": scope, "filters": filters or []}


def chart(fieldname, name, label, document_type, scope, based_on, timespan="Last Year", interval="Monthly",
          type="Bar", filters=None):
	"""A Count time-series chart. frappe_theme's form-scoped Group By charts splice the browser's filter
	object into the query as a string (controllers/chart.py `chart_doc_type`), so they are not offered."""
	return {"kind": "chart", "fieldname": fieldname, "name": name, "label": label, "document_type": document_type,
	        "scope": scope, "chart_type": "Count", "based_on": based_on, "timespan": timespan,
	        "interval": interval, "type": type, "filters": filters or []}


def html(fieldname, block, label):
	return {"kind": "html", "fieldname": fieldname, "block": block, "label": label}


def row(*blocks):
	"""Blocks laid side by side in one section (one column each)."""
	return {"kind": "row", "blocks": list(blocks)}


SUBMITTED = [["docstatus", "=", 1]]
NOT_CANCELLED = [["docstatus", "!=", 2]]

# Custom HTML Block name -> mount key understood by public/js/form_ux.js and api/form_summary.py
HTML_BLOCKS = {
	"SF AI Summary": "ai_summary",
	"SF Patient Header": "patient_header",
	"SF Patient Glance": "patient_glance",
	"SF Patient Results": "patient_results",
	"SF Patient Upcoming": "patient_upcoming",
	"SF Patient History": "patient_history",
	"SF Vitals Trend": "vitals_trend",
	"SF Encounter History": "encounter_history",
	"SF Inpatient Stay": "inpatient_stay",
	"SF Practitioner Day": "practitioner_day",
	"SF Emergency Triage": "emergency_triage",
	"SF Lab Previous Results": "lab_previous_results",
	"SF Observation Trend": "observation_trend",
	"SF Therapy Progress": "therapy_progress",
	"SF Insurance Utilisation": "insurance_utilisation",
	"SF Claim Breakdown": "claim_breakdown",
	"SF Unit Occupancy": "unit_occupancy",
}

FORMS = {
	# Read top to bottom: who the patient is (with quick actions), what could cause harm next to the AI summary,
	# vitals, results and what is coming, then one timeline of visits. Compact lists with "view all" links,
	# not list views: the complete record sets are on the Records tab. Blocks carry their own headings.
	"Patient": [
		html("sf_patient_header", "SF Patient Header", ""),
		row(html("sf_patient_glance", "SF Patient Glance", ""), html("sf_ai_summary", "SF AI Summary", "")),
		html("sf_vitals_trend", "SF Vitals Trend", ""),
		row(html("sf_patient_results", "SF Patient Results", ""), html("sf_patient_upcoming", "SF Patient Upcoming", "")),
		html("sf_patient_history", "SF Patient History", ""),
	],
	"Patient Encounter": [
		html("sf_ai_summary", "SF AI Summary", "AI summary"),
		html("sf_encounter_history", "SF Encounter History", "Patient history"),
		html("sf_vitals_trend", "SF Vitals Trend", "Vitals trend"),
		table("sf_tbl_service_requests", "Orders from this encounter", "Service Request", "order_group",
		      [col("template_dn", "Order", "Dynamic Link", 3), col("template_dt", "Type", "Link"), col("status", "Status", "Link")]),
		table("sf_tbl_medication_requests", "Medications from this encounter", "Medication Request", "order_group",
		      [col("medication", "Medication", "Link", 3), col("dosage", "Dosage", "Link"), col("period", "Period", "Link"),
		       col("status", "Status", "Link")]),
		table("sf_tbl_vitals", "Vitals recorded in this encounter", "Vital Signs", "encounter",
		      [col("signs_date", "Date", "Date"), col("bp_systolic", "Systolic"), col("bp_diastolic", "Diastolic"),
		       col("pulse", "Pulse"), col("temperature", "Temp")],
		      crud=("read", "create")),
	],
	"Inpatient Record": [
		html("sf_ai_summary", "SF AI Summary", "AI summary"),
		html("sf_inpatient_stay", "SF Inpatient Stay", "Stay"),
		row(
			card("sf_card_vitals", "SF Admission Vitals Recorded", "Vitals recorded", "Vital Signs", "inpatient_record", SUBMITTED),
			card("sf_card_lab_tests", "SF Admission Lab Tests", "Lab tests", "Lab Test", "inpatient_record", NOT_CANCELLED),
		),
		html("sf_vitals_trend", "SF Vitals Trend", "Vitals during the stay"),
		table("sf_tbl_medication_orders", "Medication orders", "Inpatient Medication Order", "inpatient_record",
		      [col("start_date", "Start", "Date"), col("completed_orders", "Given", "Float"), col("total_orders", "Ordered", "Float"),
		       col("status", "Status", "Select")]),
		table("sf_tbl_nursing", "Nursing tasks", "Nursing Task", "inpatient_record",
		      [col("activity", "Activity", "Link", 3), col("requested_start_time", "Due", "Datetime"), col("status", "Status", "Select")]),
		table("sf_tbl_lab_tests", "Lab tests", "Lab Test", "inpatient_record",
		      [col("result_date", "Result date", "Date"), col("lab_test_name", "Test", "Data", 3), col("status", "Status", "Select")]),
		table("sf_tbl_vitals", "Vital signs", "Vital Signs", "inpatient_record",
		      [col("signs_date", "Date", "Date"), col("signs_time", "Time", "Time"), col("bp_systolic", "Systolic"),
		       col("bp_diastolic", "Diastolic"), col("pulse", "Pulse"), col("temperature", "Temp")], crud=("read", "create")),
	],
	"Healthcare Practitioner": [
		html("sf_practitioner_day", "SF Practitioner Day", "Today"),
		row(
			card("sf_card_encounters", "SF Practitioner Encounters", "Encounters", "Patient Encounter", "practitioner", SUBMITTED),
			card("sf_card_lab_tests", "SF Practitioner Lab Tests", "Lab tests ordered", "Lab Test", "practitioner", NOT_CANCELLED),
		),
		chart("sf_chart_encounters", "SF Practitioner Encounters per Week", "Encounters per week", "Patient Encounter",
		      "practitioner", based_on="encounter_date", timespan="Last Quarter", interval="Weekly", type="Line",
		      filters=SUBMITTED),
		table("sf_tbl_upcoming", "Upcoming appointments", "Patient Appointment", "practitioner",
		      [col("appointment_date", "Date", "Date"), col("appointment_time", "Time", "Time"),
		       col("patient_name", "Patient", "Data", 3), col("status", "Status", "Select")],
		      conditions=[["appointment_date", ">=", TODAY]]),
		table("sf_tbl_encounters", "Recent encounters", "Patient Encounter", "practitioner",
		      [col("encounter_date", "Date", "Date"), col("patient_name", "Patient", "Data", 3), col("status", "Status", "Select")]),
	],
	"Emergency Record": [
		html("sf_ai_summary", "SF AI Summary", "AI summary"),
		html("sf_emergency_triage", "SF Emergency Triage", "Triage & timers"),
		table("sf_tbl_vitals", "Observations", "Observation", "reference_docname",
		      [col("observation_template", "Observation", "Link", 3), col("result_data", "Result"),
		       col("permitted_unit", "Unit", "Link"), col("status", "Status", "Select")],
		      connection="Referenced", dt_reference_field="reference_doctype", dn_reference_field="reference_docname"),
		table("sf_tbl_service_requests", "Orders", "Service Request", "emergency_record",
		      [col("template_dn", "Order", "Dynamic Link", 3), col("status", "Status", "Link")]),
		table("sf_tbl_lab_tests", "Lab tests", "Lab Test", "emergency_record",
		      [col("result_date", "Result date", "Date"), col("lab_test_name", "Test", "Data", 3), col("status", "Status", "Select")]),
	],
	"Lab Test": [
		html("sf_lab_previous", "SF Lab Previous Results", "Previous results for this test"),
	],
	"Observation": [
		html("sf_observation_trend", "SF Observation Trend", "Trend"),
		table("sf_tbl_components", "Component results", "Observation", "parent_observation",
		      [col("observation_template", "Observation", "Link", 3), col("result_data", "Result"),
		       col("permitted_unit", "Unit", "Link"), col("status", "Status", "Select")]),
	],
	"Diagnostic Report": [
		table("sf_tbl_observations", "Observations", "Observation", "reference_docname",
		      [col("observation_template", "Observation", "Link", 3), col("result_data", "Result"),
		       col("permitted_unit", "Unit", "Link"), col("status", "Status", "Select")],
		      connection="Indirect", local_field="sample_collection", foreign_field="reference_docname"),
	],
	"Sample Collection": [
		table("sf_tbl_reports", "Diagnostic reports", "Diagnostic Report", "sample_collection",
		      [col("name", "Report", "Link", 3), col("status", "Status", "Select")]),
		table("sf_tbl_observations", "Observations", "Observation", "reference_docname",
		      [col("observation_template", "Observation", "Link", 3), col("result_data", "Result"),
		       col("status", "Status", "Select")],
		      connection="Referenced", dt_reference_field="reference_doctype", dn_reference_field="reference_docname"),
	],
	"Therapy Plan": [
		html("sf_ai_summary", "SF AI Summary", "AI summary"),
		html("sf_therapy_progress", "SF Therapy Progress", "Progress"),
		card("sf_card_sessions", "SF Therapy Sessions Done", "Sessions done", "Therapy Session", "therapy_plan", SUBMITTED),
		table("sf_tbl_sessions", "Therapy sessions", "Therapy Session", "therapy_plan",
		      [col("start_date", "Date", "Date"), col("therapy_type", "Therapy", "Link", 3),
		       col("total_counts_targeted", "Target", "Int"), col("total_counts_completed", "Done", "Int")]),
		table("sf_tbl_assessments", "Assessments", "Patient Assessment", "patient",
		      [col("assessment_datetime", "When", "Datetime"), col("assessment_template", "Assessment", "Link", 3),
		       col("total_score_obtained", "Score", "Int"), col("total_score", "Out of", "Int")],
		      connection="Indirect", local_field="patient", foreign_field="patient"),
	],
	"Patient Insurance Policy": [
		html("sf_insurance_utilisation", "SF Insurance Utilisation", "Utilisation"),
		card("sf_card_claims", "SF Policy Claims", "Claims", "Insurance Claim", "insurance_policy", SUBMITTED),
		table("sf_tbl_coverages", "Coverages", "Patient Insurance Coverage", "insurance_policy",
		      [col("posting_date", "Date", "Date"), col("template_dn", "Service", "Dynamic Link", 3),
		       col("coverage", "Coverage %", "Percent"), col("status", "Status", "Select")]),
		table("sf_tbl_claims", "Claims", "Insurance Claim", "insurance_policy",
		      [col("posting_date", "Date", "Date"), col("insurance_claim_amount", "Claimed", "Currency"),
		       col("approved_amount", "Approved", "Currency"), col("status", "Status", "Select")]),
	],
	"Insurance Claim": [
		html("sf_claim_breakdown", "SF Claim Breakdown", "Claim breakdown"),
	],
	"Healthcare Service Unit": [
		html("sf_unit_occupancy", "SF Unit Occupancy", "Occupancy"),
	],
}

# Module that owns the cards/charts/blocks this app creates for each form (one of this app's modules).
FORM_MODULE = {
	"Patient": "Outpatient", "Patient Encounter": "Outpatient", "Healthcare Practitioner": "Outpatient",
	"Inpatient Record": "Inpatient", "Emergency Record": "Emergency", "Lab Test": "Diagnostics",
	"Observation": "Diagnostics", "Diagnostic Report": "Diagnostics", "Sample Collection": "Diagnostics",
	"Therapy Plan": "Rehabilitation", "Patient Insurance Policy": "Billing and Insurance",
	"Insurance Claim": "Billing and Insurance", "Healthcare Service Unit": "Healthcare Setup",
}


# The forms' own fields, regrouped (form_ux/apply.py): `order` is the native fields' order after the Details
# tab, with `sections` (new sf_* Section / Column Breaks) placed in it; `labels` relabel native fields; `hidden`
# hides them. A native field the order leaves out is kept, at the end of Details, and reported by spec_problems.
NATIVE_LAYOUT = {
	"Patient": {
		"sections": {
			"sf_sec_contact": ("Section Break", "Contact"),
			"sf_col_contact": ("Column Break", ""),
			"sf_sec_preferences": ("Section Break", "Preferences and consent"),
		},
		"order": [
			# Identity
			"basic_info", "naming_series", "first_name", "middle_name", "last_name", "patient_name", "sex",
			"blood_group", "dob", "age_html", "image", "column_break_14", "status", "uid", "inpatient_record",
			"inpatient_status", "emergency_record",
			# Contact, then address and family (from the old Address & Contact tab)
			"sf_sec_contact", "mobile", "phone", "email", "sf_col_contact", "invite_user", "user_id",
			"address_contacts", "address_html", "column_break_22", "contact_html", "sb_relation", "patient_relation",
			# Preferences and consent
			"sf_sec_preferences", "report_preference", "spice_no_cloud_ai",
			# Billing, notes
			"customer_details_section", "customer", "customer_group", "territory", "country", "column_break_24",
			"default_currency", "default_price_list", "language", "more_info", "patient_details",
			# Medical history tab
			"medical_history_tab", "personal_and_social_history", "occupation", "column_break_25", "marital_status",
			"allergy_medical_and_surgical_history", "allergies", "medication", "column_break_20", "medical_history",
			"surgical_history", "risk_factors", "tobacco_past_use", "tobacco_current_use", "alcohol_past_use",
			"alcohol_current_use", "column_break_32", "surrounding_factors", "other_risk_factors",
			# emptied by the move above, then the full list of linked records last
			"address_and_contact_tab", "dashboard_tab",
		],
		"labels": {
			"basic_info": "Identity",
			"address_contacts": "Addresses and contacts",
			"sb_relation": "Family and relations",
			"customer_details_section": "Billing",
			"more_info": "Notes",
			"medical_history_tab": "Medical history",
			"allergies": "Allergies (notes; recorded allergies are listed on the Overview)",
			"dashboard_tab": "Records",
		},
		"hidden": ["address_and_contact_tab"],
		# where the Details tab ends: native fields the order does not name are kept just before these
		"trailing_tabs": ("medical_history_tab", "address_and_contact_tab", "dashboard_tab"),
	},
}
