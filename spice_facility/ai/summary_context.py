"""Grounding context for AI summaries and record questions: the open record plus its related records.

Reuses the Overview builders in api/form_summary.py so the AI sees exactly what the form shows, then adds
what a clinician needs and the form does not list (recent lab values, the encounter's own orders).
Everything is bounded; `context_hash` ignores time-relative values so a summary goes stale only when the
underlying records change, not because the clock moved.
"""

import hashlib
import json

import frappe
from frappe import _

from spice_facility.ai.clinical_flags import flags_for, vital_trends
from spice_facility.api import form_summary as fs

LAB_HISTORY = 5
# Keys whose values depend on "now" rather than on the records
VOLATILE_KEYS = {"days", "timers", "upcoming_week", "date", "days_to_expiry"}
SUPPORTED = ("Patient", "Patient Encounter", "Inpatient Record", "Emergency Record", "Therapy Plan")


def recent_lab_results(patient, filters=None, limit=LAB_HISTORY):
	tests = fs._related("Lab Test", {"patient": patient, "docstatus": 1, **(filters or {})},
	                    ["name", "lab_test_name", "result_date", "lab_test_comment"], "creation desc", limit)
	if not tests:
		return []
	values = {}
	for row in frappe.get_all("Normal Test Result", {"parent": ["in", [t.name for t in tests]], "parenttype": "Lab Test"},
	                          ["parent", "lab_test_event", "lab_test_name", "result_value", "lab_test_uom", "normal_range"],
	                          order_by="idx asc", limit=limit * 30):
		label = row.lab_test_event or row.lab_test_name
		values.setdefault(row.parent, []).append(
			{"parameter": label, "value": row.result_value, "unit": row.lab_test_uom, "normal_range": row.normal_range})
	return [{"test": t.lab_test_name, "date": t.result_date, "results": values.get(t.name, []),
	         "comment": t.lab_test_comment, "record": f"Lab Test:{t.name}"} for t in tests]


def _encounter_orders(doc):
	return {
		"symptoms": [s.complaint for s in doc.symptoms],
		"diagnoses": [d.diagnosis for d in doc.diagnosis],
		"medications": [{"medication": m.medication or m.drug_code, "dosage": m.dosage, "period": m.period}
		                for m in doc.drug_prescription],
		"lab_orders": [l.lab_test_code or l.observation_template for l in doc.lab_test_prescription],
		"procedures": [p.procedure for p in doc.procedure_prescription],
		"note": doc.encounter_comment,
	}


def inpatient_medications(inpatient_record, limit=10):
	"""Distinct drugs ordered during the admission, with dose form and how many doses were given."""
	orders = fs._related("Inpatient Medication Order", {"inpatient_record": inpatient_record, "docstatus": 1},
	                     ["name"], "creation desc", 5)
	if not orders:
		return []
	drugs = {}
	for row in frappe.get_all("Inpatient Medication Order Entry", {"parent": ["in", [o.name for o in orders]],
	                                                                "parenttype": "Inpatient Medication Order"},
	                          ["drug_name", "drug", "dosage", "dosage_form", "is_completed", "instructions"], limit=300):
		# Health stores the item code as drug_name; the owning Medication carries the clinical name.
		label = frappe.db.get_value("Medication Linked Item", {"item_code": row.drug, "parenttype": "Medication"},
		                            "parent") or row.drug_name or row.drug
		entry = drugs.setdefault(label, {"drug": label, "dose": row.dosage,
		                                                     "form": row.dosage_form, "instructions": row.instructions,
		                                                     "doses_ordered": 0, "doses_given": 0})
		entry["doses_ordered"] += 1
		entry["doses_given"] += 1 if row.is_completed else 0
	return list(drugs.values())[:limit]


def active_diagnoses(patient, limit=8):
	encounters = fs._related("Patient Encounter", {"patient": patient, "docstatus": 1}, ["name"], "encounter_date desc", 10)
	if not encounters:
		return []
	rows = frappe.get_all("Patient Encounter Diagnosis", {"parent": ["in", [e.name for e in encounters]],
	                                                       "parenttype": "Patient Encounter"}, ["diagnosis"], limit=50)
	return list(dict.fromkeys(r.diagnosis for r in rows))[:limit]


def build_context(doc):
	"""The record's grounding context, plus abnormal flags and vital trends computed in code."""
	context = _base_context(doc)
	patient = doc.name if doc.doctype == "Patient" else doc.get("patient")
	sex = frappe.db.get_value("Patient", patient, "sex") if patient else None
	context["abnormal"] = flags_for(context, sex)
	context["vital_trends"] = vital_trends(context.get("vitals"))
	context["must_mention"] = must_mention(context)
	return context


def must_mention(context):
	"""Facts the summary has to cover, picked in code so a small model cannot skip them."""
	facts = []
	stay = context.get("stay") or {}
	if stay.get("admitted"):
		day = max(1, -(-int(stay.get("days") or 0) // 1)) if stay.get("days") else 1
		facts.append(f"Day {day} of stay" + (f" of {stay['expected_days']} expected" if stay.get("expected_days") else "")
		             + f" ({stay.get('status')})")
	diagnoses = context.get("active_diagnoses") or (context.get("admission") or {}).get("diagnosis") or \
		(context.get("encounter") or {}).get("diagnoses")
	if diagnoses:
		facts.append("Diagnoses: " + ", ".join(diagnoses))
	facts += [a["text"].split(" — ")[0] for a in (context.get("abnormal") or [])[:4]]
	facts += [t for t in (context.get("vital_trends") or []) if not t.startswith("Temperature 98")][:3]
	meds = context.get("medications") or (context.get("patient") or {}).get("medications") or []
	names = [m.get("drug") or m.get("medication") for m in meds if (m.get("drug") or m.get("medication"))]
	if names:
		facts.append("Medications: " + ", ".join(names[:6]))
	allergies = (context.get("patient") or {}).get("allergies") or (context.get("history") or {}).get("allergies") or []
	if allergies:
		facts.append("Allergies: " + ", ".join(a.get("allergy") for a in allergies))
	return facts


def _base_context(doc):
	if doc.doctype not in SUPPORTED:
		frappe.throw(_("AI summaries are not available for {0}").format(doc.doctype), frappe.ValidationError)
	record = {"record": f"{doc.doctype}:{doc.name}"}
	if doc.doctype == "Patient":
		header = fs.patient_header(doc)
		header.pop("currency", None)
		return {**record, "patient": header, "active_diagnoses": active_diagnoses(doc.name),
		        "vitals": fs._vitals(doc.name, limit=6),
		        "recent_encounters": fs._related("Patient Encounter", {"patient": doc.name, "docstatus": 1},
		                                         ["name", "encounter_date", "practitioner_name", "encounter_comment"],
		                                         "encounter_date desc", 5),
		        "labs": recent_lab_results(doc.name)}
	if doc.doctype == "Patient Encounter":
		return {**record, "encounter": {"date": doc.encounter_date, "practitioner": doc.practitioner_name,
		                                "department": doc.medical_department, **_encounter_orders(doc)},
		        "history": fs.encounter_history(doc), "vitals": fs._vitals(doc.patient, limit=4),
		        "labs": recent_lab_results(doc.patient)}
	if doc.doctype == "Inpatient Record":
		return {**record, "admission": {"status": doc.status, "chief_complaint": [c.complaint for c in doc.chief_complaint],
		                                "diagnosis": [d.diagnosis for d in doc.diagnosis],
		                                "instructions": doc.admission_instruction},
		        "stay": fs.inpatient_stay(doc), "vitals": fs._vitals(doc.patient, doc.name, 10),
		        "medications": inpatient_medications(doc.name),
		        "labs": recent_lab_results(doc.patient, limit=3),
		        "pending_nursing_tasks": fs._related("Nursing Task", {"inpatient_record": doc.name,
		                                                              "status": ["not in", ["Completed", "Cancelled"]]},
		                                             ["activity", "requested_start_time"], "requested_start_time asc", 10)}
	if doc.doctype == "Emergency Record":
		return {**record, "emergency": fs.emergency_triage(doc), "patient": doc.patient_name}
	return {**record, "therapy": fs.therapy_progress(doc),
	        "plan": [{"therapy_type": d.therapy_type, "sessions": d.no_of_sessions} for d in doc.therapy_plan_details]}


def _stable(value):
	if isinstance(value, dict):
		return {k: _stable(v) for k, v in value.items() if k not in VOLATILE_KEYS}
	if isinstance(value, list):
		return [_stable(v) for v in value]
	return value


def context_hash(context):
	return hashlib.sha256(json.dumps(_stable(context), sort_keys=True, default=str).encode()).hexdigest()


def serialise(context, max_chars):
	text = json.dumps(context, default=str, separators=(",", ":"))
	return text if len(text) <= max_chars else text[:max_chars] + "…(truncated)"


def record_refs(context):
	"""Every `Doctype:name` the context mentions; the only records an answer may link to."""
	refs = set()

	def walk(value):
		if isinstance(value, dict):
			for key, inner in value.items():
				if key == "record" and isinstance(inner, str) and ":" in inner:
					refs.add(inner)
				walk(inner)
		elif isinstance(value, list):
			for inner in value:
				walk(inner)

	walk(context)
	return refs
