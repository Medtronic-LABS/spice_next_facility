"""Data for the Overview-tab HTML blocks (rendered by public/js/form_ux.js).

One whitelisted entry point, `get_block`, checks the caller can read the open document, then runs the
block's builder. Builders read related records through `frappe.get_list`, so the caller's permissions
apply to those too, and every query is capped.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, get_datetime, getdate, now_datetime, nowdate, time_diff_in_seconds

ROW_CAP = 20
HISTORY_CAP = 6


def _num(value):
	"""Vital Signs stores most readings as Data; keep numbers, drop blanks and text."""
	try:
		return flt(value) if value not in (None, "") else None
	except (TypeError, ValueError):
		return None


def _reading(value):
	"""A vital sign of 0 means "not measured" (Health leaves BMI at 0 without a height), never a reading."""
	number = _num(value)
	return number if number and number > 0 else None


def _currency():
	return frappe.defaults.get_global_default("currency")


def _related(doctype, filters, fields, order_by="creation desc", limit=ROW_CAP):
	return frappe.get_list(doctype, filters=filters, fields=fields, order_by=order_by, limit_page_length=limit)


def _readable(doctype, filters, fields, order_by="creation desc", limit=ROW_CAP):
	"""`_related` for a panel that combines several doctypes: one the user cannot read is left out, not an error."""
	return _related(doctype, filters, fields, order_by, limit) if frappe.has_permission(doctype, "read") else []


# The patient's records counted on the Patient card, each opening its list: (doctype, filters beyond `patient`)
PATIENT_RECORD_COUNTS = (("Patient Encounter", {"docstatus": 1}), ("Lab Test", {"docstatus": ["!=", 2]}),
                         ("Vital Signs", {"docstatus": 1}))


def _record_counts(patient):
	"""How many of each record the patient has; doctypes the user cannot read are left out."""
	return [{"doctype": doctype, "patient": patient, "count": frappe.db.count(doctype, {"patient": patient, **filters})}
	        for doctype, filters in PATIENT_RECORD_COUNTS if frappe.has_permission(doctype, "read")]


# ─── builders ───────────────────────────────────────────────────────────────


def patient_header(doc):
	today = nowdate()
	allergies = _readable("Patient Allergy", {"patient": doc.name, "status": "Active"},
	                      ["allergy", "severity", "reaction"], "severity asc")
	medications = _readable("Medication Request",
	                        {"patient": doc.name, "docstatus": 1, "status": ["like", "active%"]},
	                        ["medication", "dosage", "period", "order_date"], "order_date desc", 8)
	last_visit = _readable("Patient Encounter", {"patient": doc.name, "docstatus": 1},
	                       ["encounter_date", "practitioner_name"], "encounter_date desc", 1)
	next_visit = _readable("Patient Appointment",
	                       {"patient": doc.name, "appointment_date": [">=", today], "status": ["not in", ["Cancelled", "No Show"]]},
	                       ["appointment_date", "appointment_time", "practitioner_name"], "appointment_date asc, appointment_time asc", 1)
	policy = _readable("Patient Insurance Policy",
	                   {"patient": doc.name, "docstatus": 1, "policy_expiry_date": [">=", today]},
	                   ["insurance_payor", "policy_number", "policy_expiry_date"], "policy_expiry_date desc", 1)
	outstanding = 0
	if doc.customer:
		invoices = _readable("Sales Invoice", {"customer": doc.customer, "docstatus": 1, "outstanding_amount": [">", 0]},
		                     ["outstanding_amount"], limit=200)
		outstanding = sum(flt(i.outstanding_amount) for i in invoices)

	admission = None
	if doc.inpatient_record:
		ip = frappe.db.get_value("Inpatient Record", doc.inpatient_record,
		                         ["name", "status", "admitted_datetime"], as_dict=True)
		bed = frappe.get_all("Inpatient Occupancy", {"parent": doc.inpatient_record, "left": 0}, pluck="service_unit", limit=1)
		admission = {**ip, "bed": bed[0] if bed else None} if ip else None

	emergency = None
	if doc.emergency_record:
		er = frappe.db.get_value("Emergency Record", doc.emergency_record,
		                         ["name", "status", "triage_level", "arrival_datetime"], as_dict=True)
		if er and er.status not in ("Closed", "Cancelled"):
			emergency = er

	return {
		"patient": doc.name, "patient_name": doc.patient_name, "sex": doc.sex, "age": doc.get_age() if doc.dob else None,
		"blood_group": doc.blood_group, "mobile": doc.mobile, "status": doc.status,
		"allergies": allergies, "medications": medications,
		"last_visit": last_visit[0] if last_visit else None, "next_visit": next_visit[0] if next_visit else None,
		"policy": policy[0] if policy else None, "outstanding": outstanding, "currency": _currency(),
		"admission": admission, "emergency": emergency, "records": _record_counts(doc.name),
		"actions": patient_quick_actions(),
	}


# Records a user starts from a patient (the card's quick actions), each opened with the patient filled in
PATIENT_QUICK_ACTIONS = ("Patient Appointment", "Patient Encounter", "Vital Signs", "Lab Test")
UPCOMING_CAP = 5
RESULTS_CAP = 5
TIMELINE_CAP = 8
PENDING_CAP = 99


def patient_quick_actions():
	"""The quick actions this user may take (create permission), in display order."""
	return [doctype for doctype in PATIENT_QUICK_ACTIONS if frappe.has_permission(doctype, "create")]


def patient_glance(doc):
	"""What could cause harm, on one panel: allergies, active medications, problems and risk factors.

	Problems are the diagnoses of recent encounters (the same rule as the AI summary), plus the free-text
	history; risks are the present tobacco / alcohol use and other risk factors recorded on the patient."""
	from spice_facility.ai.summary_context import active_diagnoses

	allergies = _readable("Patient Allergy", {"patient": doc.name, "status": "Active"},
	                      ["name", "allergy", "severity", "reaction"], "severity asc")
	medications = _readable("Medication Request",
	                        {"patient": doc.name, "docstatus": 1, "status": ["like", "active%"]},
	                        ["name", "medication", "dosage", "period"], "order_date desc", 8)
	risks = [{"label": label, "value": doc.get(field)} for field, label in (
		("tobacco_current_use", _("Tobacco")), ("alcohol_current_use", _("Alcohol")),
		("other_risk_factors", _("Other risks")), ("surrounding_factors", _("Occupational / environmental")))
		if doc.get(field)]
	return {
		"patient": doc.name, "allergies": allergies, "medications": medications,
		"problems": active_diagnoses(doc.name) if frappe.has_permission("Patient Encounter", "read") else [], "history": doc.medical_history, "risks": risks,
		"can_add_allergy": frappe.has_permission("Patient Allergy", "create"),
	}


def patient_results(doc):
	"""The latest lab tests, each with how many of its results are outside the normal range."""
	from spice_facility.ai.clinical_flags import lab_flags
	from spice_facility.ai.summary_context import recent_lab_results

	if not frappe.has_permission("Lab Test", "read"):
		return {"patient": doc.name, "results": [], "pending": 0}
	tests = recent_lab_results(doc.name, limit=RESULTS_CAP)
	results = []
	for test in tests:
		flags = lab_flags([test], doc.sex)
		results.append({"name": test["record"].split(":", 1)[1], "test": test["test"], "date": test["date"],
		                "abnormal": [{"parameter": f["parameter"], "text": _short_flag(f["text"])} for f in flags]})
	# get_list, not db.count: only the drafts this user may open (user permissions apply)
	pending = len(frappe.get_list("Lab Test", filters={"patient": doc.name, "docstatus": 0}, pluck="name",
	                              limit_page_length=PENDING_CAP))
	return {"patient": doc.name, "results": results, "pending": pending}


def _short_flag(text):
	"""'HbA1c 8.2 % (high; normal <5.7 …) — HbA1c, 2026-10-09' -> 'HbA1c 8.2 % high' (the card shows test and date)."""
	reading = text.split(" (", 1)[0]
	direction = re.search(r"\((high|low);", text)
	return f"{reading} {_(direction.group(1))}" if direction else reading


def patient_upcoming(doc):
	"""Appointments still to come, nearest first."""
	rows = _readable("Patient Appointment",
	                 {"patient": doc.name, "appointment_date": [">=", nowdate()], "status": ["not in", ["Cancelled", "No Show"]]},
	                 ["name", "appointment_date", "appointment_time", "practitioner_name", "department", "status"],
	                 "appointment_date asc, appointment_time asc", UPCOMING_CAP)
	return {"patient": doc.name, "appointments": rows,
	        "can_book": frappe.has_permission("Patient Appointment", "create")}


def patient_history(doc):
	"""One timeline of the patient's visits: encounters, emergency visits and admissions, newest first."""
	events = []
	events += [{"doctype": "Patient Encounter", "name": r.name, "date": r.encounter_date, "title": r.medical_department,
	            "who": r.practitioner_name, "status": r.status}
	           for r in _readable("Patient Encounter", {"patient": doc.name, "docstatus": ["!=", 2]},
	                              ["name", "encounter_date", "medical_department", "practitioner_name", "status"],
	                              "encounter_date desc", TIMELINE_CAP)]
	events += [{"doctype": "Emergency Record", "name": r.name, "date": getdate(r.arrival_datetime) if r.arrival_datetime else None,
	            "title": r.triage_level, "who": None, "status": r.status}
	           for r in _readable("Emergency Record", {"patient": doc.name},
	                              ["name", "arrival_datetime", "triage_level", "status"], "arrival_datetime desc", TIMELINE_CAP)]
	events += [{"doctype": "Inpatient Record", "name": r.name,
	            "date": getdate(r.admitted_datetime) if r.admitted_datetime else None,
	            "title": r.medical_department, "who": r.primary_practitioner, "status": r.status}
	           for r in _readable("Inpatient Record", {"patient": doc.name},
	                              ["name", "admitted_datetime", "medical_department", "primary_practitioner", "status"],
	                              "admitted_datetime desc", TIMELINE_CAP)]
	events.sort(key=lambda e: str(e["date"] or ""), reverse=True)
	kinds = sorted({e["doctype"] for e in events})
	return {"patient": doc.name, "events": events[:TIMELINE_CAP], "kinds": kinds}


def _vitals(patient, inpatient_record=None, limit=ROW_CAP):
	filters = {"patient": patient, "docstatus": 1}
	if inpatient_record:
		filters["inpatient_record"] = inpatient_record
	rows = _related("Vital Signs", filters,
	                ["signs_date", "signs_time", "bp_systolic", "bp_diastolic", "pulse", "temperature",
	                 "respiratory_rate", "bmi", "weight"],
	                "signs_date desc, signs_time desc", limit)
	return [
		{"date": r.signs_date, "time": r.signs_time, "bp_systolic": _reading(r.bp_systolic),
		 "bp_diastolic": _reading(r.bp_diastolic), "pulse": _reading(r.pulse), "temperature": _reading(r.temperature),
		 "respiratory_rate": _reading(r.respiratory_rate), "bmi": _reading(r.bmi), "weight": _reading(r.weight)}
		for r in reversed(rows)
	]


def vitals_trend(doc):
	if doc.doctype == "Patient":
		return {"points": _vitals(doc.name)}
	if doc.doctype == "Inpatient Record":
		return {"points": _vitals(doc.patient, doc.name)}
	return {"points": _vitals(doc.patient)}


def encounter_history(doc):
	previous = _related("Patient Encounter", {"patient": doc.patient, "docstatus": 1, "name": ["!=", doc.name]},
	                    ["name", "encounter_date", "practitioner_name", "medical_department", "encounter_comment"],
	                    "encounter_date desc", HISTORY_CAP)
	diagnoses = {}
	if previous:
		for row in frappe.get_all("Patient Encounter Diagnosis", {"parent": ["in", [p.name for p in previous]],
		                                                          "parenttype": "Patient Encounter"},
		                          ["parent", "diagnosis"], limit=HISTORY_CAP * 10):
			diagnoses.setdefault(row.parent, []).append(row.diagnosis)
	allergies = _related("Patient Allergy", {"patient": doc.patient, "status": "Active"}, ["allergy", "severity"])
	return {
		"encounters": [{**p, "diagnoses": diagnoses.get(p.name, [])} for p in previous],
		"allergies": allergies,
	}


def inpatient_stay(doc):
	admitted = get_datetime(doc.admitted_datetime) if doc.admitted_datetime else None
	end = get_datetime(doc.discharge_datetime) if doc.discharge_datetime else now_datetime()
	return {
		"status": doc.status, "admitted": doc.admitted_datetime, "discharged": doc.discharge_datetime,
		"expected_days": cint(doc.expected_length_of_stay),
		"days": round(time_diff_in_seconds(end, admitted) / 86400, 1) if admitted else 0,
		"practitioner": frappe.db.get_value("Healthcare Practitioner", doc.primary_practitioner, "practitioner_name")
		if doc.primary_practitioner else None,
		"occupancies": [{"service_unit": o.service_unit, "check_in": o.check_in, "check_out": o.check_out,
		                 "current": not o.left} for o in doc.inpatient_occupancies],
	}


def practitioner_day(doc):
	today = nowdate()
	appointments = _related("Patient Appointment", {"practitioner": doc.name, "appointment_date": today},
	                        ["name", "appointment_time", "patient_name", "status", "appointment_type"],
	                        "appointment_time asc", 40)
	upcoming = frappe.db.count("Patient Appointment", {"practitioner": doc.name,
	                                                   "appointment_date": ["between", [frappe.utils.add_days(today, 1),
	                                                                                    frappe.utils.add_days(today, 7)]],
	                                                   "status": ["not in", ["Cancelled", "No Show"]]})
	return {"date": today, "appointments": appointments, "upcoming_week": upcoming}


def emergency_triage(doc):
	level = frappe.db.get_value("Triage Level", doc.triage_level, ["color", "priority", "target_reassessment_mins"],
	                            as_dict=True) if doc.triage_level else None
	now = now_datetime()
	arrival = get_datetime(doc.arrival_datetime) if doc.arrival_datetime else None

	def minutes(start, end):
		return round(time_diff_in_seconds(get_datetime(end), get_datetime(start)) / 60) if start and end else None

	vitals = _related("Observation", {"reference_doctype": "Emergency Record", "reference_docname": doc.name},
	                  ["observation_template", "result_data", "permitted_unit", "creation"], "creation desc")
	return {
		"status": doc.status, "triage_level": doc.triage_level, "level": level, "arrival_mode": doc.arrival_mode,
		"chief_complaint": doc.chief_complaint, "disposition": doc.disposition,
		"timers": {
			"door_to_triage": minutes(arrival, doc.triage_datetime),
			"in_department": minutes(arrival, doc.disposition_datetime or now),
			"closed": bool(doc.disposition_datetime),
		},
		"vitals": vitals,
	}


def lab_previous_results(doc):
	tests = [doc.name] + [t.name for t in _related("Lab Test", {"patient": doc.patient, "template": doc.template,
	                                                            "docstatus": 1, "name": ["!=", doc.name]},
	                                               ["name"], "creation desc", 4)]
	dates = {t.name: t.date or t.result_date or getdate(t.submitted_date or t.creation)
	         for t in frappe.get_all("Lab Test", {"name": ["in", tests]}, ["name", "date", "result_date", "submitted_date", "creation"])}
	results = frappe.get_all("Normal Test Result", {"parent": ["in", tests], "parenttype": "Lab Test"},
	                         ["parent", "lab_test_event", "lab_test_name", "result_value", "lab_test_uom", "normal_range", "idx"],
	                         order_by="idx asc", limit=len(tests) * 30)
	rows = {}
	for r in results:
		key = r.lab_test_event or r.lab_test_name
		row = rows.setdefault(key, {"name": key, "uom": r.lab_test_uom, "normal_range": r.normal_range, "values": {}})
		row["values"][r.parent] = r.result_value
	return {"columns": [{"name": t, "date": dates.get(t), "current": t == doc.name} for t in tests],
	        "rows": list(rows.values())}


def observation_trend(doc):
	points = _related("Observation", {"patient": doc.patient, "observation_template": doc.observation_template,
	                                  "docstatus": ["!=", 2]},
	                  ["name", "result_data", "result_float", "posting_date", "creation"], "creation desc", ROW_CAP)
	return {
		"template": doc.observation_template, "unit": doc.permitted_unit,
		"points": [{"name": p.name, "date": p.posting_date or getdate(p.creation),
		            "value": _num(p.result_float) or _num(p.result_data), "current": p.name == doc.name}
		           for p in reversed(points)],
	}


def therapy_progress(doc):
	sessions = _related("Therapy Session", {"therapy_plan": doc.name, "docstatus": 1},
	                    ["start_date", "therapy_type", "total_counts_targeted", "total_counts_completed"],
	                    "start_date asc", ROW_CAP)
	assessments = _related("Patient Assessment", {"patient": doc.patient, "docstatus": 1},
	                       ["assessment_datetime", "assessment_template", "total_score_obtained", "total_score"],
	                       "assessment_datetime asc", ROW_CAP)
	return {
		"total": cint(doc.total_sessions), "completed": cint(doc.total_sessions_completed), "status": doc.status,
		"sessions": [{"date": s.start_date, "therapy_type": s.therapy_type,
		              "completion": round(100 * flt(s.total_counts_completed) / flt(s.total_counts_targeted))
		              if flt(s.total_counts_targeted) else None} for s in sessions],
		"assessments": assessments,
	}


def insurance_utilisation(doc):
	claims = _related("Insurance Claim", {"insurance_policy": doc.name, "docstatus": 1},
	                  ["insurance_claim_amount", "approved_amount", "paid_amount", "outstanding_amount", "rejected_amount"],
	                  limit=200)
	coverages = _related("Patient Insurance Coverage", {"insurance_policy": doc.name, "docstatus": 1},
	                     ["status", "coverage_amount"], limit=500)
	by_status = {}
	for c in coverages:
		by_status[c.status] = by_status.get(c.status, 0) + 1

	def total(field):
		return sum(flt(c.get(field)) for c in claims)

	return {
		"payor": doc.insurance_payor, "policy_number": doc.policy_number, "expiry": doc.policy_expiry_date,
		"days_to_expiry": date_diff(doc.policy_expiry_date, nowdate()) if doc.policy_expiry_date else None,
		"covered": sum(flt(c.coverage_amount) for c in coverages), "coverage_status": by_status,
		"claimed": total("insurance_claim_amount"), "approved": total("approved_amount"),
		"paid": total("paid_amount"), "outstanding": total("outstanding_amount"), "rejected": total("rejected_amount"),
		"currency": _currency(),
	}


def claim_breakdown(doc):
	by_status = {}
	for c in doc.coverages:
		by_status[c.status] = by_status.get(c.status, 0) + 1
	return {
		"claimed": flt(doc.insurance_claim_amount), "approved": flt(doc.approved_amount), "paid": flt(doc.paid_amount),
		"outstanding": flt(doc.outstanding_amount), "rejected": flt(doc.rejected_amount),
		"status": doc.status, "coverage_status": by_status, "currency": _currency(),
	}


def unit_occupancy(doc):
	units = frappe.get_all("Healthcare Service Unit",
	                       {"lft": [">=", doc.lft], "rgt": ["<=", doc.rgt], "is_group": 0, "inpatient_occupancy": 1},
	                       ["name", "healthcare_service_unit_name", "occupancy_status", "service_unit_type"],
	                       order_by="lft asc", limit=200)
	occupants = {}
	if units:
		for o in frappe.get_all("Inpatient Occupancy", {"service_unit": ["in", [u.name for u in units]], "left": 0,
		                                                "parenttype": "Inpatient Record"},
		                        ["service_unit", "parent", "check_in"], limit=200):
			occupants[o.service_unit] = o
		names = frappe.get_all("Inpatient Record", {"name": ["in", [o.parent for o in occupants.values()]]},
		                       ["name", "patient_name"]) if occupants else []
		patient_of = {r.name: r.patient_name for r in names}
	beds = []
	for u in units:
		o = occupants.get(u.name)
		beds.append({"name": u.name, "label": u.healthcare_service_unit_name, "status": u.occupancy_status or "Vacant",
		             "type": u.service_unit_type, "inpatient_record": o.parent if o else None,
		             "patient": patient_of.get(o.parent) if o else None, "since": o.check_in if o else None})
	occupied = sum(1 for b in beds if b["inpatient_record"])
	return {"is_group": cint(doc.is_group), "beds": beds, "occupied": occupied, "vacant": len(beds) - occupied}


BUILDERS = {
	"patient_header": (("Patient",), patient_header),
	"patient_glance": (("Patient",), patient_glance),
	"patient_results": (("Patient",), patient_results),
	"patient_upcoming": (("Patient",), patient_upcoming),
	"patient_history": (("Patient",), patient_history),
	"vitals_trend": (("Patient", "Patient Encounter", "Inpatient Record"), vitals_trend),
	"encounter_history": (("Patient Encounter",), encounter_history),
	"inpatient_stay": (("Inpatient Record",), inpatient_stay),
	"practitioner_day": (("Healthcare Practitioner",), practitioner_day),
	"emergency_triage": (("Emergency Record",), emergency_triage),
	"lab_previous_results": (("Lab Test",), lab_previous_results),
	"observation_trend": (("Observation",), observation_trend),
	"therapy_progress": (("Therapy Plan",), therapy_progress),
	"insurance_utilisation": (("Patient Insurance Policy",), insurance_utilisation),
	"claim_breakdown": (("Insurance Claim",), claim_breakdown),
	"unit_occupancy": (("Healthcare Service Unit",), unit_occupancy),
}


@frappe.whitelist()
def get_block(block: str, doctype: str, name: str) -> dict:
	if block not in BUILDERS:
		frappe.throw(_("Unknown summary block {0}").format(block), frappe.ValidationError)
	doctypes, builder = BUILDERS[block]
	if doctype not in doctypes:
		frappe.throw(_("Summary block {0} does not apply to {1}").format(block, doctype), frappe.ValidationError)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	return builder(frappe.get_doc(doctype, name))
