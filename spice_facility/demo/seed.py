"""Demo data across every Frappe Health module, telling one patient story per flow.

    bench --site <site> execute spice_facility.demo.seed.run
    bench --site <site> execute spice_facility.demo.seed.run --kwargs "{'company': 'MDT'}"

Re-runnable: masters are get-or-create, and a patient's story is seeded only once. Never runs on
install; demo data is opt-in.
"""

import json
import traceback

import frappe
from frappe.utils import add_days, getdate, today

from spice_facility.setup.fixes import apply_fixes

PRICE_LIST = "Standard Selling"
# Bound by run(): the target company, its abbreviation and the seeding date.
COMPANY = None
ABBR = None
TODAY = None


def d(offset):
	return add_days(TODAY, offset)


def log(msg):
	print(f"  - {msg}")


def get_or_create(doctype, filters, data=None, submit=False):
	name = frappe.db.exists(doctype, filters)
	if name:
		return frappe.get_doc(doctype, name)
	doc = frappe.get_doc({"doctype": doctype, **(filters if isinstance(filters, dict) else {}), **(data or {})})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


# ─── masters ────────────────────────────────────────────────────────────────


def seed_basics():
	print("Basics")
	apply_fixes()
	for uom in ("mg", "Day", "ml"):
		get_or_create("UOM", {"uom_name": uom})
	for dept in ("General Medicine", "Paediatrics"):
		get_or_create("Medical Department", {"department": dept})
	for uom, desc in (
		("g/dL", "grams per decilitre"),
		("mg/dL", "milligrams per decilitre"),
		("cells/cumm", "cells per cubic millimetre"),
		("lakh/cumm", "lakh per cubic millimetre"),
		("million/cumm", "million per cubic millimetre"),
	):
		get_or_create("Lab Test UOM", {"lab_test_uom": uom}, {"uom_description": desc})


def service_item(code, name, rate):
	get_or_create(
		"Item",
		{"item_code": code},
		{"item_name": name, "item_group": "Services", "stock_uom": "Nos", "is_stock_item": 0, "is_sales_item": 1},
	)
	get_or_create("Item Price", {"item_code": code, "price_list": PRICE_LIST}, {"price_list_rate": rate})
	return code


def seed_service_units():
	print("Service units")
	service_item("CONSULT-OPD", "OPD Consultation", 500)
	service_item("CONSULT-IPD", "Inpatient Visit", 800)

	get_or_create(
		"Healthcare Service Unit Type",
		{"service_unit_type": "OPD Consultation Room"},
		{"allow_appointments": 1, "overlap_appointments": 0},
	)
	get_or_create("Healthcare Service Unit Type", {"service_unit_type": "Laboratory"}, {"allow_appointments": 1, "overlap_appointments": 1})
	get_or_create(
		"Healthcare Service Unit Type",
		{"service_unit_type": "General Ward Bed"},
		{
			"inpatient_occupancy": 1,
			"is_billable": 1,
			"item_code": "BED-GENERAL",
			"item_group": "Services",
			"uom": "Day",
			"no_of_hours": 24,
			"rate": 1500,
			"medical_department": "General Medicine",
		},
	)

	root = f"All Healthcare Service Units - {ABBR}"

	def unit(label, unit_type=None, parent=root, is_group=0, capacity=None):
		return get_or_create(
			"Healthcare Service Unit",
			{"healthcare_service_unit_name": label, "company": COMPANY},
			{
				"service_unit_type": unit_type,
				"parent_healthcare_service_unit": parent,
				"is_group": is_group,
				"service_unit_capacity": capacity,
			},
		).name

	units = {
		"opd1": unit("OPD Room 1", "OPD Consultation Room"),
		"opd2": unit("OPD Room 2", "OPD Consultation Room"),
		"opd3": unit("OPD Room 3", "OPD Consultation Room"),
		"lab": unit("Central Laboratory", "Laboratory", capacity=4),
	}
	ward = unit("General Ward", is_group=1)
	for i in range(1, 5):
		units[f"bed{i}"] = unit(f"General Ward Bed {i}", "General Ward Bed", parent=ward, capacity=1)
	return units


def seed_schedule():
	print("Practitioner schedule")
	slots = []
	for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"):
		for h in range(9, 13):
			for m in (0, 15, 30, 45):
				end_h, end_m = (h, m + 15) if m < 45 else (h + 1, 0)
				slots.append({"day": day, "from_time": f"{h:02d}:{m:02d}:00", "to_time": f"{end_h:02d}:{end_m:02d}:00"})
	return get_or_create("Practitioner Schedule", {"schedule_name": "Weekday OPD Morning"}, {"time_slots": slots}).name


def seed_practitioners(units, schedule):
	print("Practitioners")
	specs = [
		("ananya", "Ananya", "Rao", "Female", "General Medicine", units["opd1"], 500),
		("vikram", "Vikram", "Mehta", "Male", "Cardiology", units["opd2"], 800),
		("fatima", "Fatima", "Sheikh", "Female", "Gynaecology", units["opd3"], 600),
	]
	out = {}
	for key, first, last, gender, dept, room, fee in specs:
		out[key] = get_or_create(
			"Healthcare Practitioner",
			{"first_name": first, "last_name": last},
			{
				"status": "Active",
				"gender": gender,
				"department": dept,
				"practitioner_type": "Internal",
				"mobile_phone": f"98450{len(out) + 1:05d}",
				"practitioner_schedules": [{"schedule": schedule, "service_unit": room}],
				"op_consulting_charge_item": "CONSULT-OPD",
				"op_consulting_charge": fee,
				"inpatient_visit_charge_item": "CONSULT-IPD",
				"inpatient_visit_charge": 800,
			},
		).name
	return out


def seed_appointment_types():
	print("Appointment types")
	for name, mins, color in (
		("Consultation", 15, "#3b82f6"),
		("Follow-up", 10, "#10b981"),
		("Antenatal Check-up", 20, "#ec4899"),
	):
		get_or_create(
			"Appointment Type",
			{"appointment_type": name},
			{"allow_booking_for": "Practitioner", "default_duration": mins, "color": color, "price_list": PRICE_LIST},
		)


def seed_clinical_vocab():
	print("Complaints & diagnoses")
	for c in (
		"Fever", "Cough", "Sore Throat", "Headache", "Chest Pain", "Breathlessness",
		"Fatigue", "Polyuria", "Excessive Thirst", "Dizziness", "Wheezing", "Nausea",
	):
		get_or_create("Complaint", {"complaints": c})
	for dx in (
		"Type 2 Diabetes Mellitus", "Essential Hypertension", "Acute Upper Respiratory Infection",
		"Iron Deficiency Anaemia", "Supervision of Normal Pregnancy", "Community-acquired Pneumonia",
		"Stable Angina (suspected)", "Acute Bronchiolitis",
	):
		get_or_create("Diagnosis", {"diagnosis": dx})


def duration(number, period):
	name = frappe.db.exists("Prescription Duration", {"number": number, "period": period})
	return name or get_or_create("Prescription Duration", {"number": number, "period": period}).name


def seed_medications():
	print("Medications")
	specs = [
		("Metformin", 500, "Tablet", "BID", (1, "Month"), 2.5),
		("Amlodipine", 5, "Tablet", "Once Daily", (1, "Month"), 3),
		("Atorvastatin", 10, "Tablet", "Once Bedtime", (1, "Month"), 6),
		("Paracetamol", 500, "Tablet", "TID", (5, "Day"), 1.5),
		("Cetirizine", 10, "Tablet", "Once Bedtime", (5, "Day"), 2),
		("Amoxicillin", 500, "Capsule", "TID", (7, "Day"), 8),
		("Ferrous Sulfate", 200, "Tablet", "Once Daily", (3, "Month"), 1),
		("Folic Acid", 5, "Tablet", "Once Daily", (3, "Month"), 1),
		("Salbutamol", 2, "Syrup", "TID", (5, "Day"), 45),
	]
	meds = {}
	for generic, strength, form, dosage, (n, period), rate in specs:
		get_or_create("Medication Class", {"medication_class": generic})
		med = get_or_create(
			"Medication",
			{"generic_name": generic},
			{
				"medication_class": generic,
				"strength": strength,
				"strength_uom": "mg",
				"dosage_form": form,
				"default_prescription_dosage": dosage,
				"default_prescription_duration": duration(n, period),
			},
		)
		if not med.linked_items:
			med.price_list = PRICE_LIST
			med.append("linked_items", {
				"item_code": f"DRUG-{generic.upper().replace(' ', '-')}-{strength}",
				"item_group": "Drug",
				"stock_uom": "Nos",
				"is_billable": 1,
				"rate": rate,
				"description": f"{generic} {strength} mg {form}",
			})
			med.save(ignore_permissions=True)
		meds[generic] = med
	return meds


def seed_lab_templates():
	print("Lab test templates")

	def tpl(name, code, dept, rate, kind, **extra):
		return get_or_create(
			"Lab Test Template",
			{"lab_test_name": name},
			{
				"lab_test_code": code,
				"department": dept,
				"lab_test_group": "Laboratory",
				"is_billable": 1,
				"lab_test_rate": rate,
				"lab_test_template_type": kind,
				**extra,
			},
		).name

	def row(event, uom, rng):
		return {"lab_test_event": event, "lab_test_uom": uom, "normal_range": rng}

	return {
		"CBC": tpl(
			"Complete Blood Count", "LAB-CBC", "Haematology", 350, "Compound",
			normal_test_templates=[
				row("Haemoglobin", "g/dL", "M 13-17, F 12-15"),
				row("Total WBC Count", "cells/cumm", "4000-11000"),
				row("Platelet Count", "lakh/cumm", "1.5-4.5"),
				row("RBC Count", "million/cumm", "4.5-5.5"),
			],
		),
		"HBA1C": tpl("HbA1c", "LAB-HBA1C", "Biochemistry", 450, "Single",
			lab_test_uom="%", lab_test_normal_range="<5.7 normal; 5.7-6.4 prediabetes; >=6.5 diabetes"),
		"FBS": tpl("Fasting Blood Sugar", "LAB-FBS", "Biochemistry", 100, "Single",
			lab_test_uom="mg/dL", lab_test_normal_range="70-100"),
		"LIPID": tpl(
			"Lipid Profile", "LAB-LIPID", "Biochemistry", 600, "Compound",
			normal_test_templates=[
				row("Total Cholesterol", "mg/dL", "<200"),
				row("LDL Cholesterol", "mg/dL", "<100"),
				row("HDL Cholesterol", "mg/dL", ">40"),
				row("Triglycerides", "mg/dL", "<150"),
			],
		),
		"CREAT": tpl("Serum Creatinine", "LAB-CREAT", "Biochemistry", 200, "Single",
			lab_test_uom="mg/dL", lab_test_normal_range="0.6-1.2"),
	}


def seed_procedures():
	print("Clinical procedure templates")
	out = {}
	for key, name, code, dept, rate, desc in (
		("ECG", "ECG (12-lead)", "PROC-ECG", "Cardiology", 300, "Resting 12-lead electrocardiogram"),
		("NEB", "Nebulization", "PROC-NEB", "Paediatrics", 150, "Salbutamol nebulization for bronchospasm"),
		("DRESS", "Wound Dressing", "PROC-DRESS", "General Surgery", 200, "Cleaning and dressing of a minor wound"),
	):
		out[key] = get_or_create(
			"Clinical Procedure Template",
			{"template": name},
			{"item_code": code, "item_group": "Services", "medical_department": dept,
			 "is_billable": 1, "rate": rate, "description": desc, "default_duration": 900},
		).name
	return out


def seed_patients():
	print("Patients")
	specs = [
		("ramesh", "Ramesh", "Kumar", "Male", "1968-03-12", "B Positive", "Married", "Shopkeeper",
		 "Type 2 diabetes since 2018; hypertension since 2020", "", "Current smoker, 5/day"),
		("sunita", "Sunita", "Devi", "Female", "1994-07-22", "O Positive", "Married", "Homemaker",
		 "G2P1, LMP 2026-05-10", "", ""),
		("irfan", "Mohammed", "Irfan", "Male", "1985-11-05", "A Positive", "Married", "Driver",
		 "Family history of coronary artery disease", "", "Smoker, 10/day"),
		("priya", "Priya", "Nair", "Female", "2001-02-14", "O Negative", "Single", "Student",
		 "", "Penicillin", ""),
		("lakshmi", "Lakshmi", "Iyer", "Female", "1958-09-30", "AB Positive", "Widow", "Retired teacher",
		 "Menopause; mild osteoarthritis", "", ""),
		("arjun", "Arjun", "Singh", "Male", "2016-05-18", "B Negative", "Single", "Student",
		 "Recurrent wheeze since age 4", "Dust", ""),
		("kavita", "Kavita", "Sharma", "Female", "1979-12-01", "A Negative", "Married", "Bank clerk",
		 "Hypertension since 2023", "", ""),
		("rajesh", "Rajesh", "Patel", "Male", "1972-04-09", "O Positive", "Married", "Farmer",
		 "COPD (mild)", "Sulfa drugs", "Ex-smoker"),
	]
	out = {}
	for i, (key, first, last, sex, dob, bg, marital, job, history, allergy, tobacco) in enumerate(specs, start=1):
		out[key] = get_or_create(
			"Patient",
			{"first_name": first, "last_name": last},
			{
				"sex": sex, "dob": dob, "blood_group": bg, "marital_status": marital, "occupation": job,
				"mobile": f"98765{i:05d}", "country": "India", "medical_history": history,
				"allergies": allergy, "tobacco_current_use": tobacco,
			},
		).name
	return out


# ─── clinical flow helpers ──────────────────────────────────────────────────


def book(patient, practitioner, unit, appt_type, date, time, status=None, notes=None):
	appt = frappe.get_doc({
		"doctype": "Patient Appointment",
		"appointment_type": appt_type,
		"appointment_for": "Practitioner",
		"company": COMPANY,
		"patient": patient,
		"practitioner": practitioner,
		"department": frappe.db.get_value("Healthcare Practitioner", practitioner, "department"),
		"service_unit": unit,
		"appointment_date": date,
		"appointment_time": time,
		"duration": frappe.db.get_value("Appointment Type", appt_type, "default_duration"),
		"notes": notes,
	})
	appt.insert(ignore_permissions=True)
	if status:
		appt.db_set("status", status)
	return appt


def vitals(patient, date, time, encounter=None, appointment=None, **values):
	doc = frappe.get_doc({
		"doctype": "Vital Signs", "patient": patient, "company": COMPANY,
		"signs_date": date, "signs_time": time, "encounter": encounter, "appointment": appointment, **values,
	})
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def encounter(appt, complaints, diagnoses, meds=(), labs=(), procedures=(), comment=None, submit=True):
	enc = frappe.get_doc({
		"doctype": "Patient Encounter",
		"appointment": appt.name,
		"appointment_type": appt.appointment_type,
		"patient": appt.patient,
		"practitioner": appt.practitioner,
		"medical_department": appt.department,
		"company": COMPANY,
		"encounter_date": appt.appointment_date,
		"encounter_time": appt.appointment_time,
		"symptoms": [{"complaint": c} for c in complaints],
		"diagnosis": [{"diagnosis": dx} for dx in diagnoses],
		"drug_prescription": [
			{
				"medication": m.name,
				"drug_code": m.linked_items[0].item_code,
				"dosage_form": m.dosage_form,
				"dosage": m.default_prescription_dosage,
				"period": m.default_prescription_duration,
				"strength": m.strength,
				"strength_uom": m.strength_uom,
			}
			for m in meds
		],
		"lab_test_prescription": [{"lab_test_code": t} for t in labs],
		"procedure_prescription": [{"procedure": p} for p in procedures],
		"encounter_comment": comment,
	})
	enc.insert(ignore_permissions=True)
	if submit:
		enc.submit()
	return enc


def lab_results(enc, results, submit=True):
	"""Create Lab Tests from the encounter's lab Service Requests and record results."""
	from healthcare.healthcare.doctype.service_request.service_request import make_lab_test

	created = []
	for sr in frappe.get_all(
		"Service Request",
		filters={"order_group": enc.name, "template_dt": "Lab Test Template"},
		fields=["name", "template_dn"],
	):
		lt = make_lab_test(sr.name)
		lt.insert(ignore_permissions=True)
		values = results.get(sr.template_dn)
		if values is None:
			created.append(lt)
			continue
		for row in lt.normal_test_items:
			key = row.lab_test_event or row.lab_test_name
			if isinstance(values, dict) and key in values:
				row.result_value = values[key]
			elif not isinstance(values, dict):
				row.result_value = values
		lt.lab_test_comment = results.get(f"{sr.template_dn}:comment")
		lt.save(ignore_permissions=True)
		if submit:
			lt.submit()
		created.append(lt)
	return created


def complete_procedure(enc):
	from healthcare.healthcare.doctype.service_request.service_request import make_clinical_procedure

	for sr in frappe.get_all(
		"Service Request",
		filters={"order_group": enc.name, "template_dt": "Clinical Procedure Template"},
		pluck="name",
	):
		proc = make_clinical_procedure(sr)
		proc.start_date = enc.encounter_date
		proc.start_time = enc.encounter_time
		proc.notes = "Sinus rhythm, HR 92, ST depression 1mm in V4-V6. Refer for stress test."
		proc.insert(ignore_permissions=True)
		proc.start_procedure()
		proc.reload()
		proc.complete_procedure()


def invoice(patient, pay=False):
	from healthcare.healthcare.utils import get_healthcare_services_to_invoice

	customer = frappe.db.get_value("Patient", patient, "customer")
	services = get_healthcare_services_to_invoice(patient, customer, COMPANY)
	if not services:
		return None
	si = frappe.new_doc("Sales Invoice")
	si.update({"patient": patient, "customer": customer, "company": COMPANY, "due_date": TODAY,
	           "selling_price_list": PRICE_LIST})
	item_fields = {df.fieldname for df in frappe.get_meta("Sales Invoice Item").fields}
	for s in services:
		row = {k: v for k, v in s.items() if k in item_fields}
		row.update({
			"item_code": s.get("service"),
			"qty": s.get("qty") or 1,
			"rate": s.get("rate"),
			"reference_dt": s.get("reference_type"),
			"reference_dn": s.get("reference_name"),
		})
		si.append("items", row)
	si.set_missing_values()
	si.insert(ignore_permissions=True)
	si.submit()
	if pay:
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", si.name)
		pe.mode_of_payment = "Cash"
		pe.reference_no = f"CASH-{si.name}"
		pe.reference_date = TODAY
		pe.insert(ignore_permissions=True)
		pe.submit()
	return si


def admit(enc, practitioner, bed, check_in):
	from healthcare.healthcare.doctype.inpatient_record.inpatient_record import admit_patient, schedule_inpatient

	schedule_inpatient({
		"patient": enc.patient,
		"admission_encounter": enc.name,
		"referring_practitioner": practitioner,
		"admission_ordered_for": enc.encounter_date,
		"admission_service_unit_type": "General Ward Bed",
		"expected_length_of_stay": 5,
		"admission_instruction": "IV antibiotics, O2 if SpO2 < 92%, 4-hourly vitals",
		"medical_department": "General Medicine",
		"primary_practitioner": practitioner,
	})
	ip = frappe.get_last_doc("Inpatient Record", filters={"patient": enc.patient})
	admit_patient(ip, bed, check_in, expected_discharge=add_days(check_in, 5))
	return ip


def has_story(patient):
	return frappe.db.exists("Patient Appointment", {"patient": patient})


# ─── patient stories ────────────────────────────────────────────────────────


def seed_stories(p, dr, units, meds, labs, procs):
	print("Clinical stories")
	M = meds

	# 1. Ramesh — chronic diabetes + hypertension: full loop, billed and paid, follow-up today.
	if not has_story(p["ramesh"]):
		a = book(p["ramesh"], dr["ananya"], units["opd1"], "Consultation", d(-28), "09:30:00")
		e = encounter(a, ["Polyuria", "Excessive Thirst", "Fatigue"],
			["Type 2 Diabetes Mellitus", "Essential Hypertension"],
			meds=[M["Metformin"], M["Amlodipine"], M["Atorvastatin"]],
			labs=[labs["HBA1C"], labs["FBS"], labs["LIPID"], labs["CREAT"]],
			comment="Poorly controlled DM. Counselled on diet, exercise, smoking cessation. Review with reports in 4 weeks.")
		vitals(p["ramesh"], d(-28), "09:25:00", encounter=e.name, appointment=a.name,
			temperature="98.4", pulse="84", respiratory_rate="16", bp_systolic="152", bp_diastolic="94",
			height=1.70, weight=82)
		lab_results(e, {
			labs["HBA1C"]: "8.2", f"{labs['HBA1C']}:comment": "Poor glycaemic control",
			labs["FBS"]: "168",
			labs["LIPID"]: {"Total Cholesterol": "236", "LDL Cholesterol": "152", "HDL Cholesterol": "38", "Triglycerides": "210"},
			labs["CREAT"]: "1.0",
		})
		invoice(p["ramesh"], pay=True)
		book(p["ramesh"], dr["ananya"], units["opd1"], "Follow-up", d(0), "10:00:00",
			notes="Review HbA1c, lipid profile; titrate Metformin")
		log("Ramesh Kumar — DM/HTN consult, 4 lab results, invoiced + paid, follow-up today")

	# 2. Sunita — antenatal care: ANC visit done, low Hb flagged, next ANC booked.
	if not has_story(p["sunita"]):
		a = book(p["sunita"], dr["fatima"], units["opd3"], "Antenatal Check-up", d(-14), "10:00:00")
		e = encounter(a, ["Fatigue", "Nausea"], ["Supervision of Normal Pregnancy"],
			meds=[M["Folic Acid"], M["Ferrous Sulfate"]], labs=[labs["CBC"], labs["FBS"]],
			comment="21 weeks. Fundal height appropriate. FHR 144/min. Advised iron-rich diet.")
		vitals(p["sunita"], d(-14), "09:55:00", encounter=e.name, appointment=a.name,
			temperature="98.2", pulse="88", bp_systolic="112", bp_diastolic="72", height=1.58, weight=61)
		lab_results(e, {
			labs["CBC"]: {"Haemoglobin": "10.4", "Total WBC Count": "8200", "Platelet Count": "2.6", "RBC Count": "3.9"},
			f"{labs['CBC']}:comment": "Mild anaemia of pregnancy",
			labs["FBS"]: "86",
		})
		invoice(p["sunita"])
		book(p["sunita"], dr["fatima"], units["opd3"], "Antenatal Check-up", d(14), "10:00:00",
			notes="ANC visit 3 — repeat CBC, anomaly scan review")
		log("Sunita Devi — ANC visit, Hb 10.4 flagged, invoiced (unpaid), next ANC in 2 weeks")

	# 3. Mohammed Irfan — chest pain seen today, ECG ordered and completed.
	if not has_story(p["irfan"]):
		a = book(p["irfan"], dr["vikram"], units["opd2"], "Consultation", d(0), "09:15:00")
		e = encounter(a, ["Chest Pain", "Breathlessness"], ["Stable Angina (suspected)"],
			meds=[M["Atorvastatin"]], labs=[labs["LIPID"]], procedures=[procs["ECG"]],
			comment="Exertional retrosternal pain x 2 weeks, relieved by rest. ECG today; TMT to be scheduled.")
		vitals(p["irfan"], d(0), "09:10:00", encounter=e.name, appointment=a.name,
			temperature="98.6", pulse="92", respiratory_rate="18", bp_systolic="138", bp_diastolic="88",
			height=1.72, weight=79)
		complete_procedure(e)
		lab_results(e, {labs["LIPID"]: None})  # sample collected, results pending
		log("Mohammed Irfan — cardiology consult today, ECG completed, lipid profile pending")

	# 4. Priya — acute URTI, CBC result still pending.
	if not has_story(p["priya"]):
		a = book(p["priya"], dr["ananya"], units["opd1"], "Consultation", d(-3), "11:00:00")
		e = encounter(a, ["Fever", "Cough", "Sore Throat", "Headache"], ["Acute Upper Respiratory Infection"],
			meds=[M["Paracetamol"], M["Cetirizine"]], labs=[labs["CBC"]],
			comment="Penicillin allergy — antibiotics withheld. Viral URTI likely. Steam inhalation, fluids.")
		vitals(p["priya"], d(-3), "10:55:00", encounter=e.name, appointment=a.name,
			temperature="101.2", pulse="102", respiratory_rate="20", bp_systolic="110", bp_diastolic="70",
			tongue="Coated", height=1.62, weight=54)
		lab_results(e, {labs["CBC"]: None})
		log("Priya Nair — URTI, CBC draft (results pending)")

	# 5. Lakshmi — iron deficiency anaemia, follow-up next week.
	if not has_story(p["lakshmi"]):
		a = book(p["lakshmi"], dr["ananya"], units["opd1"], "Consultation", d(-21), "11:30:00")
		e = encounter(a, ["Fatigue", "Breathlessness", "Dizziness"], ["Iron Deficiency Anaemia"],
			meds=[M["Ferrous Sulfate"], M["Folic Acid"]], labs=[labs["CBC"], labs["CREAT"]],
			comment="Pallor ++. Stool occult blood advised. Repeat CBC after 4 weeks of oral iron.")
		vitals(p["lakshmi"], d(-21), "11:25:00", encounter=e.name, appointment=a.name,
			temperature="98.0", pulse="96", bp_systolic="124", bp_diastolic="78", height=1.55, weight=52)
		lab_results(e, {
			labs["CBC"]: {"Haemoglobin": "8.9", "Total WBC Count": "6100", "Platelet Count": "3.1", "RBC Count": "3.4"},
			f"{labs['CBC']}:comment": "Microcytic hypochromic picture",
			labs["CREAT"]: "0.9",
		})
		invoice(p["lakshmi"])
		book(p["lakshmi"], dr["ananya"], units["opd1"], "Follow-up", d(7), "11:30:00", notes="Repeat CBC")
		log("Lakshmi Iyer — anaemia, Hb 8.9, invoiced (unpaid), follow-up next week")

	# 6. Arjun — paediatric wheeze, booked for later today (waiting in queue).
	if not has_story(p["arjun"]):
		book(p["arjun"], dr["ananya"], units["opd1"], "Consultation", d(0), "11:00:00", status="Checked In",
			notes="Cough and wheeze x 3 days, worse at night")
		log("Arjun Singh — checked in for today, encounter not started")

	# 7. Kavita — hypertension: past visit, a no-show, and an upcoming review.
	if not has_story(p["kavita"]):
		a = book(p["kavita"], dr["vikram"], units["opd2"], "Consultation", d(-50), "10:30:00")
		e = encounter(a, ["Headache", "Dizziness"], ["Essential Hypertension"], meds=[M["Amlodipine"]],
			comment="Started on Amlodipine 5mg. Home BP log advised.")
		vitals(p["kavita"], d(-50), "10:25:00", encounter=e.name, appointment=a.name,
			pulse="78", bp_systolic="158", bp_diastolic="98", height=1.60, weight=70)
		book(p["kavita"], dr["vikram"], units["opd2"], "Follow-up", d(-20), "10:30:00", status="No Show")
		book(p["kavita"], dr["vikram"], units["opd2"], "Follow-up", d(3), "10:30:00", notes="BP review after missed visit")
		log("Kavita Sharma — HTN visit, one no-show, review in 3 days")

	# 8. Rajesh — pneumonia admitted to General Ward from OPD.
	if not has_story(p["rajesh"]):
		a = book(p["rajesh"], dr["vikram"], units["opd2"], "Consultation", d(-2), "12:00:00")
		e = encounter(a, ["Fever", "Cough", "Breathlessness"], ["Community-acquired Pneumonia"],
			meds=[M["Amoxicillin"], M["Paracetamol"]], labs=[labs["CBC"], labs["CREAT"]],
			comment="SpO2 90% on room air, right basal crepitations. Admit for IV antibiotics.")
		vitals(p["rajesh"], d(-2), "11:55:00", encounter=e.name, appointment=a.name,
			temperature="102.4", pulse="112", respiratory_rate="26", bp_systolic="118", bp_diastolic="76",
			height=1.68, weight=66)
		lab_results(e, {
			labs["CBC"]: {"Haemoglobin": "13.8", "Total WBC Count": "15800", "Platelet Count": "2.2", "RBC Count": "4.7"},
			f"{labs['CBC']}:comment": "Neutrophilic leucocytosis",
			labs["CREAT"]: "1.1",
		})
		admit(e, dr["vikram"], units["bed1"], f"{d(-2)} 14:00:00")
		log("Rajesh Patel — pneumonia, admitted to General Ward Bed 1")


# ═══ phase 2: remaining Healthcare modules ══════════════════════════════════


def section(title, fn, *args):
	"""Run one seeding section in its own savepoint so a failure doesn't sink the rest."""
	print(title)
	frappe.db.savepoint("seed_section")
	try:
		result = fn(*args)
		frappe.db.commit()
		return result
	except Exception:
		frappe.db.rollback(save_point="seed_section")
		print(f"  !! FAILED: {title}")
		traceback.print_exc(limit=-4)
		return None


def make_patient(first, last, sex, dob, **extra):
	return get_or_create("Patient", {"first_name": first, "last_name": last}, {"sex": sex, "dob": dob, "country": "India", **extra}).name


def first_leaf_account(company, **filters):
	return frappe.db.get_value("Account", {"company": company, "is_group": 0, **filters}, "name")


CODE_SYSTEMS = {
	"ICD-10": "http://hl7.org/fhir/sid/icd-10",
	"LOINC": "http://loinc.org",
	"SNOMED CT": "http://snomed.info/sct",
}

DIAGNOSIS_CODES = {
	"Type 2 Diabetes Mellitus": ("E11.9", "Type 2 diabetes mellitus without complications"),
	"Essential Hypertension": ("I10", "Essential (primary) hypertension"),
	"Acute Upper Respiratory Infection": ("J06.9", "Acute upper respiratory infection, unspecified"),
	"Iron Deficiency Anaemia": ("D50.9", "Iron deficiency anaemia, unspecified"),
	"Supervision of Normal Pregnancy": ("Z34.9", "Supervision of normal pregnancy, unspecified"),
	"Community-acquired Pneumonia": ("J18.9", "Pneumonia, unspecified organism"),
	"Stable Angina (suspected)": ("I20.9", "Angina pectoris, unspecified"),
	"Acute Bronchiolitis": ("J21.9", "Acute bronchiolitis, unspecified"),
	"Dengue Fever": ("A90", "Dengue fever [classical dengue]"),
	"Hypothyroidism": ("E03.9", "Hypothyroidism, unspecified"),
	"Primary Osteoarthritis of Knee": ("M17.1", "Other primary gonarthrosis"),
	"Head Injury": ("S06.9", "Intracranial injury, unspecified"),
	"Acute Exacerbation of Asthma": ("J45.901", "Unspecified asthma with acute exacerbation"),
	"Polycystic Ovary Syndrome": ("E28.2", "Polycystic ovarian syndrome"),
	"Hypertensive Urgency": ("I16.0", "Hypertensive urgency"),
}
LAB_CODES = {
	"HbA1c": ("4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood"),
	"Fasting Blood Sugar": ("1558-6", "Fasting glucose [Mass/volume] in Serum or Plasma"),
	"Lipid Profile": ("57698-3", "Lipid panel with direct LDL - Serum or Plasma"),
	"Serum Creatinine": ("2160-0", "Creatinine [Mass/volume] in Serum or Plasma"),
	"Complete Blood Count": ("58410-2", "CBC panel - Blood by Automated count"),
}
PROCEDURE_CODES = {
	"ECG (12-lead)": ("29303009", "Electrocardiographic procedure"),
	"Nebulization": ("56251003", "Nebulizer therapy"),
	"Wound Dressing": ("182531007", "Dressing of wound"),
}


def code_row(system, code, display):
	name = frappe.db.exists("Code Value", {"code_system": system, "code_value": code})
	if not name:
		cv = frappe.get_doc({"doctype": "Code Value", "code_system": system, "code_value": code, "display": display, "definition": display})
		cv.insert(ignore_permissions=True)
		name = cv.name
	return {"code_system": system, "code_value": name, "code": code}


def attach_codes(doctype, name, rows):
	doc = frappe.get_doc(doctype, name)
	if doc.get("codification_table"):
		return
	for row in rows:
		doc.append("codification_table", row)
	doc.save(ignore_permissions=True)


def seed_terminology():
	for system, uri in CODE_SYSTEMS.items():
		get_or_create("Code System", {"code_system": system}, {"uri": uri, "is_fhir_defined": 1, "description": f"{system} ({uri})"})
	for dx, (code, display) in DIAGNOSIS_CODES.items():
		get_or_create("Diagnosis", {"diagnosis": dx})
		attach_codes("Diagnosis", dx, [code_row("ICD-10", code, display)])
	for lab, (code, display) in LAB_CODES.items():
		attach_codes("Lab Test Template", lab, [code_row("LOINC", code, display)])
	for proc, (code, display) in PROCEDURE_CODES.items():
		attach_codes("Clinical Procedure Template", proc, [code_row("SNOMED CT", code, display)])
	value_set = frappe.db.get_value("Code Value Set", {"name": ["like", "Common OPD Diagnoses%"]})
	if not value_set:
		vs = frappe.get_doc({"doctype": "Code Value Set", "value_set": "Common OPD Diagnoses", "code_system": "ICD-10",
			"system_uri": CODE_SYSTEMS["ICD-10"], "description": "ICD-10 codes most used in OPD"})
		vs.insert(ignore_permissions=True)
		value_set = vs.name
	for code, _display in DIAGNOSIS_CODES.values():
		frappe.db.set_value("Code Value", {"code_system": "ICD-10", "code_value": code}, "value_set", value_set)
	log(f"{len(CODE_SYSTEMS)} code systems, {len(DIAGNOSIS_CODES) + len(LAB_CODES) + len(PROCEDURE_CODES)} codes, codified diagnoses/labs/procedures")


def seed_more_vocab():
	for c in ("Joint Pain", "Knee Swelling", "Weight Gain", "Cold Intolerance", "Abdominal Pain", "Vomiting",
	          "Rash", "Head Injury", "Irregular Periods", "Blurred Vision", "Body Ache"):
		get_or_create("Complaint", {"complaints": c})
	for dept in ("Physiotherapy", "Accident And Emergency Care", "Diagnostic Imaging"):
		get_or_create("Medical Department", {"department": dept})
	for name, desc in (("Progress Note", "Daily progress during admission"), ("Nursing Note", "Shift nursing observations"),
	                   ("Counselling Note", "Patient / family counselling"), ("Procedure Note", "Bedside procedure details")):
		get_or_create("Clinical Note Type", {"clinical_note_type": name})
	for o, abbr in (("Escherichia coli", "E. coli"), ("Staphylococcus aureus", "S. aureus"),
	                ("Streptococcus pneumoniae", "S. pneumoniae"), ("Klebsiella pneumoniae", "K. pneumoniae"),
	                ("Pseudomonas aeruginosa", "P. aeruginosa")):
		get_or_create("Organism", {"organism": o}, {"abbr": abbr})
	for cat, care in (("Laboratory", "Diagnostic"), ("Imaging", "Diagnostic"), ("Procedure", "Intervention"),
	                  ("Therapy", "Intervention"), ("Screening", "Preventive")):
		get_or_create("Service Request Category", {"category": cat}, {"patient_care_type": care})
	for reason in ("Diagnostic work-up", "Routine monitoring", "Pre-operative evaluation", "Treatment response", "Screening"):
		get_or_create("Service Request Reason", {"service_request_reason": reason})
	log("complaints, departments, note types, organisms, request categories/reasons")


def seed_samples():
	get_or_create("Lab Test UOM", {"lab_test_uom": "ml"}, {"uom_description": "millilitre"})
	for st in ("Blood", "Urine", "Swab", "Sputum"):
		get_or_create("Sample Type", {"sample_type": st})
	for name, hexcode in (("Lavender", "#b57edc"), ("Red", "#d32f2f"), ("Yellow", "#fbc02d"), ("Grey", "#9e9e9e"), ("White", "#ffffff")):
		if not frappe.db.exists("Color", name):
			frappe.get_doc({"doctype": "Color", "color": hexcode}).insert(ignore_permissions=True, set_name=name)
	samples = {
		"EDTA Whole Blood": ("Blood", "Lavender"),
		"Serum": ("Blood", "Yellow"),
		"Fluoride Plasma": ("Blood", "Grey"),
		"Urine (Midstream)": ("Urine", "White"),
		"Throat Swab": ("Swab", "Red"),
	}
	for sample, (stype, color) in samples.items():
		get_or_create("Lab Test Sample", {"sample": sample}, {"sample_type": stype, "container_closure_color": color, "sample_uom": "ml"})
	for tpl, sample, qty in (("Complete Blood Count", "EDTA Whole Blood", 2), ("HbA1c", "EDTA Whole Blood", 2),
	                         ("Fasting Blood Sugar", "Fluoride Plasma", 2), ("Lipid Profile", "Serum", 3),
	                         ("Serum Creatinine", "Serum", 2)):
		frappe.db.set_value("Lab Test Template", tpl, {"sample": sample, "sample_qty": qty})
	log(f"{len(samples)} sample containers; lab templates linked to samples")


def obs_template(name, category, dtype, dept, **extra):
	return get_or_create("Observation Template", {"observation": name},
		{"observation_category": category, "permitted_data_type": dtype, "medical_department": dept, **extra}).name


def seed_observation_templates():
	for uom in ("uIU/mL", "ng/dL", "pg/mL", "ml"):
		get_or_create("Lab Test UOM", {"lab_test_uom": uom}, {"uom_description": uom})

	def rng(lo, hi):
		return [{"applies_to": "All", "age": "All", "reference_from": str(lo), "reference_to": str(hi),
		         "normal_from": lo, "normal_to": hi, "normal_interpretation": "Normal",
		         "short_interpretation": f"{lo}-{hi}"}]

	common = {"sample_collection_required": 1, "sample": "Serum", "sample_qty": 3, "sample_type": "Blood"}
	obs_template("TSH", "Laboratory", "Quantity", "Biochemistry", abbr="TSH", permitted_unit="uIU/mL",
		observation_reference_range=rng(0.4, 4.0), **common)
	obs_template("Free T4", "Laboratory", "Quantity", "Biochemistry", abbr="FT4", permitted_unit="ng/dL",
		observation_reference_range=rng(0.8, 1.8), **common)
	obs_template("Free T3", "Laboratory", "Quantity", "Biochemistry", abbr="FT3", permitted_unit="pg/mL",
		observation_reference_range=rng(2.3, 4.2), **common)
	obs_template("Thyroid Profile", "Laboratory", "Quantity", "Biochemistry", abbr="TFT", has_component=1,
		observation_component=[{"observation_template": t} for t in ("TSH", "Free T4", "Free T3")],
		is_billable=1, item_code="OBS-TFT", item_group="Laboratory", rate=700, **common)
	obs_template("Chest X-Ray PA View", "Imaging", "Text", "Diagnostic Imaging", abbr="CXR",
		is_billable=1, item_code="IMG-CXR", item_group="Services", rate=500)
	obs_template("Smoking Status", "Social History", "Select", "General Medicine",
		options="Never smoker\nFormer smoker\nCurrent every day smoker\nCurrent some day smoker")
	attach_codes("Observation Template", "TSH", [code_row("LOINC", "3016-3", "Thyrotropin [Units/volume] in Serum or Plasma")])
	attach_codes("Observation Template", "Free T4", [code_row("LOINC", "3024-7", "Thyroxine (T4) free [Mass/volume] in Serum or Plasma")])
	attach_codes("Observation Template", "Chest X-Ray PA View", [code_row("LOINC", "36643-5", "XR Chest 2 Views")])
	log("Thyroid Profile (TSH/FT4/FT3), Chest X-Ray, Smoking Status observation templates")


def seed_rehab_masters(units):
	for bp in ("Knee", "Shoulder", "Lower Back", "Neck", "Ankle", "Hip"):
		get_or_create("Body Part", {"body_part": bp})
	for lvl in ("Easy", "Moderate", "Hard"):
		get_or_create("Exercise Difficulty Level", {"difficulty_level": lvl})
	exercises = [
		("Quadriceps Sets", ["Knee"], "Easy", "Tighten the thigh muscle pressing the knee into the bed; hold 5 s."),
		("Straight Leg Raise", ["Knee", "Hip"], "Moderate", "Lift the straight leg to 30 cm, hold 5 s, lower slowly."),
		("Heel Slides", ["Knee"], "Easy", "Slide heel towards buttock bending the knee, then straighten."),
		("Pendulum Swing", ["Shoulder"], "Easy", "Lean forward and let the arm swing in small circles."),
		("Pelvic Tilt", ["Lower Back"], "Easy", "Lying on back, flatten the low back against the floor; hold 5 s."),
		("Bird Dog", ["Lower Back", "Hip"], "Moderate", "On all fours extend opposite arm and leg, hold 5 s."),
	]
	for name, parts, lvl, desc in exercises:
		if not frappe.db.exists("Exercise Type", {"exercise_name": name}):
			frappe.get_doc({
				"doctype": "Exercise Type", "exercise_name": name, "difficulty_level": lvl, "description": desc,
				"body_parts": [{"body_part": p} for p in parts],
				"steps_table": [{"title": "Start position", "description": "Lie on your back on a firm surface."},
				                {"title": "Movement", "description": desc}],
			}).insert(ignore_permissions=True)

	def ex(name):
		return frappe.db.get_value("Exercise Type", {"exercise_name": name})

	therapy_types = [
		("Knee Physiotherapy", "THER-KNEE", 600, ["Knee"], [("Quadriceps Sets", 30), ("Straight Leg Raise", 20), ("Heel Slides", 20)]),
		("Shoulder Mobilisation", "THER-SHLDR", 600, ["Shoulder"], [("Pendulum Swing", 30)]),
		("Low Back Pain Rehab", "THER-LBP", 550, ["Lower Back"], [("Pelvic Tilt", 20), ("Bird Dog", 15)]),
	]
	for name, code, rate, parts, exs in therapy_types:
		get_or_create("Therapy Type", {"therapy_type": name}, {
			"medical_department": "Physiotherapy", "default_duration": 45, "healthcare_service_unit": units["physio"],
			"item_code": code, "item_name": name, "item_group": "Services", "is_billable": 1, "rate": rate,
			"therapy_for": [{"body_part": p} for p in parts], "patient_care_type": "Intervention",
			"exercises": [{"exercise_type": ex(e), "difficulty_level": frappe.db.get_value("Exercise Type", ex(e), "difficulty_level"),
			               "counts_target": n, "assistance_level": "Active"} for e, n in exs],
			"codification_table": [code_row("SNOMED CT", "91251008", "Physical therapy procedure")],
		})
	get_or_create("Therapy Plan Template", {"plan_name": "Knee OA Rehab — 6 sessions"}, {
		"item_code": "THP-KNEE-6", "item_name": "Knee OA Rehab Package (6 sessions)", "item_group": "Services",
		"description": "Six supervised knee physiotherapy sessions over 3 weeks",
		"therapy_types": [{"therapy_type": "Knee Physiotherapy", "no_of_sessions": 6, "rate": 550}],
	})
	for p in ("Pain (VAS)", "Knee Flexion Range", "Quadriceps Strength", "Swelling", "Walking Tolerance"):
		get_or_create("Patient Assessment Parameter", {"assessment_parameter": p})
	get_or_create("Patient Assessment Template", {"assessment_name": "Knee Function Assessment"}, {
		"scale_min": 0, "scale_max": 10,
		"assessment_description": "0 = worst, 10 = best (Pain scored inversely: 10 = no pain)",
		"parameters": [{"assessment_parameter": p} for p in
		               ("Pain (VAS)", "Knee Flexion Range", "Quadriceps Strength", "Swelling", "Walking Tolerance")],
	})
	log("body parts, 6 exercise types, 3 therapy types, therapy plan template, assessment template")


def seed_nursing_masters():
	activities = [
		("Record Vital Signs", 600, "Vital Signs"), ("Verify ID Band & Allergies", 300, None),
		("Fall Risk Assessment", 600, None), ("Orient Patient to Ward", 900, None),
		("Administer Medication", 300, None), ("IV Cannula Care", 600, None),
		("Medication Reconciliation", 900, None), ("Discharge Education", 1200, None),
		("Remove IV Cannula", 300, None), ("Verify Surgical Consent", 300, None), ("Confirm NPO Status", 300, None),
	]
	for name, secs, task_dt in activities:
		get_or_create("Healthcare Activity", {"activity": name}, {"activity_duration": secs, "role": "Nursing User", "task_doctype": task_dt})

	def template(title, dept, tasks):
		get_or_create("Nursing Checklist Template", {"title": title}, {"department": dept, "tasks": [
			{"activity": a, "mandatory": m, "type": t, "time_offset": off, "task_duration": frappe.db.get_value("Healthcare Activity", a, "activity_duration")}
			for a, m, t, off in tasks]})

	template("General Ward Admission", "General Medicine", [
		("Verify ID Band & Allergies", 1, None, 0), ("Record Vital Signs", 1, None, 0),
		("Fall Risk Assessment", 1, None, 1800), ("Orient Patient to Ward", 0, None, 3600)])
	template("Ward Discharge", "General Medicine", [
		("Medication Reconciliation", 1, None, 0), ("Discharge Education", 1, None, 0), ("Remove IV Cannula", 1, None, 1800)])
	template("Pre-Op Checklist", "General Surgery", [
		("Verify Surgical Consent", 1, "Pre-Op", 0), ("Confirm NPO Status", 1, "Pre-Op", 0), ("Record Vital Signs", 1, "Pre-Op", 0)])
	log(f"{len(activities)} healthcare activities, 3 nursing checklist templates")


def seed_safety(p):
	interactions = [
		("Amlodipine", "Atorvastatin", "Minor", "Amlodipine modestly raises atorvastatin levels; monitor for myalgia."),
		("Ferrous Sulfate", "Calcium Gluconate", "Moderate", "Calcium reduces iron absorption; separate doses by 2 hours."),
		("Metformin", "Cetirizine", "Minor", "No clinically significant interaction expected; listed for demo."),
		("Amoxicillin", "Folic Acid", "Minor", "No significant interaction; listed for demo of alert levels."),
	]
	get_or_create("Medication Class", {"medication_class": "Sulfonamides"})
	for a, b, severity, advice in interactions:
		for cls in (a, b):
			get_or_create("Medication Class", {"medication_class": cls})
		if not frappe.db.exists("Medication Interaction", {"interactant_a": a, "interactant_b": b}):
			frappe.get_doc({"doctype": "Medication Interaction", "severity": severity,
				"interactant_a_type": "Medication Class", "interactant_a": a,
				"interactant_b_type": "Medication Class", "interactant_b": b, "advice": advice}).insert(ignore_permissions=True)

	allergies = {
		"Penicillin": ("Medication", "Amoxicillin"), "Sulfonamides": ("Medication", "Sulfonamides"),
		"House Dust Mite": ("Environment", None), "Peanut": ("Food", None),
	}
	for name, (cat, substance) in allergies.items():
		get_or_create("Allergy", {"allergy_name": name}, {"category": cat,
			**({"substance_type": "Medication Class", "substance": substance} if substance else {})})
	for patient, allergy, sev, reaction in (
		(p["priya"], "Penicillin", "Severe", "Urticaria and lip swelling after amoxicillin (2019)"),
		(p["rajesh"], "Sulfonamides", "Moderate", "Maculopapular rash"),
		(p["arjun"], "House Dust Mite", "Moderate", "Wheeze and rhinitis on exposure"),
		(p["sunita"], "Peanut", "Minor", "Oral itching"),
	):
		if not frappe.db.exists("Patient Allergy", {"patient": patient, "allergy": allergy}):
			frappe.get_doc({"doctype": "Patient Allergy", "patient": patient, "allergy": allergy, "severity": sev,
			                "status": "Active", "reaction": reaction, "recorded_on": d(-60)}).insert(ignore_permissions=True)
	log("4 medication interactions, 4 allergy masters, 4 patient allergies")


def seed_treatment_plans(dr, labs, procs, meds):
	def drug_row(m):
		return {"medication": m.name, "drug_code": m.linked_items[0].item_code, "dosage_form": m.dosage_form,
		        "dosage": m.default_prescription_dosage, "period": m.default_prescription_duration,
		        "strength": m.strength, "strength_uom": m.strength_uom}

	get_or_create("Treatment Plan Template", {"template_name": "Type 2 Diabetes — Initial Management"}, {
		"medical_department": "General Medicine", "goal": "HbA1c < 7% within 3 months; BP < 130/80",
		"description": "Standard first-visit work-up and therapy for newly diagnosed T2DM",
		"practitioners": [{"practitioner": dr["ananya"]}], "patient_age_from": 30, "patient_age_to": 80,
		"complaints": [{"complaint": "Polyuria"}, {"complaint": "Excessive Thirst"}],
		"diagnosis": [{"diagnosis": "Type 2 Diabetes Mellitus"}],
		"drugs": [drug_row(meds["Metformin"]), drug_row(meds["Atorvastatin"])],
		"items": [{"type": "Lab Test Template", "template": labs[k], "qty": 1} for k in ("HBA1C", "FBS", "LIPID", "CREAT")],
	})
	get_or_create("Treatment Plan Template", {"template_name": "Community-acquired Pneumonia — Inpatient"}, {
		"medical_department": "General Medicine", "goal": "Afebrile 48 h, SpO2 > 94% on room air before discharge",
		"practitioners": [{"practitioner": dr["vikram"]}, {"practitioner": dr["ananya"]}],
		"complaints": [{"complaint": "Fever"}, {"complaint": "Cough"}, {"complaint": "Breathlessness"}],
		"diagnosis": [{"diagnosis": "Community-acquired Pneumonia"}],
		"drugs": [drug_row(meds["Amoxicillin"]), drug_row(meds["Paracetamol"])],
		"items": [{"type": "Lab Test Template", "template": labs["CBC"], "qty": 1},
		          {"type": "Clinical Procedure Template", "template": procs["NEB"], "qty": 3}],
		"is_inpatient": 1, "treatment_counselling_required_for_ip": 1,
		"healthcare_service_unit_type": "General Ward Bed", "expected_length_of_stay": 5,
	})
	log("2 treatment plan templates (OPD diabetes, inpatient pneumonia)")


def seed_extra_units_and_staff(units, schedule):
	get_or_create("Healthcare Service Unit Type", {"service_unit_type": "Physiotherapy Room"},
		{"allow_appointments": 1, "overlap_appointments": 1, "medical_department": "Physiotherapy"})
	get_or_create("Healthcare Service Unit Type", {"service_unit_type": "Emergency Bed"}, {
		"inpatient_occupancy": 1, "is_billable": 1, "item_code": "BED-ER", "item_group": "Services",
		"uom": "Day", "no_of_hours": 24, "rate": 2500, "medical_department": "Accident And Emergency Care"})
	root = f"All Healthcare Service Units - {ABBR}"

	def unit(label, unit_type=None, parent=root, is_group=0, capacity=None):
		return get_or_create("Healthcare Service Unit", {"healthcare_service_unit_name": label, "company": COMPANY},
			{"service_unit_type": unit_type, "parent_healthcare_service_unit": parent, "is_group": is_group,
			 "service_unit_capacity": capacity}).name

	units["physio"] = unit("Physiotherapy Gym", "Physiotherapy Room", capacity=4)
	ed = unit("Emergency Department", is_group=1)
	for i in range(1, 4):
		units[f"er{i}"] = unit(f"Emergency Bay {i}", "Emergency Bed", parent=ed, capacity=1)

	extra = {}
	for key, first, last, gender, dept, room, fee in (
		("meera", "Meera", "Pillai", "Female", "Physiotherapy", units["physio"], 600),
		("sameer", "Sameer", "Khan", "Male", "Accident And Emergency Care", units["er1"], 1000),
	):
		extra[key] = get_or_create("Healthcare Practitioner", {"first_name": first, "last_name": last}, {
			"status": "Active", "gender": gender, "department": dept, "practitioner_type": "Internal",
			"practitioner_schedules": [{"schedule": schedule, "service_unit": room}],
			"op_consulting_charge_item": "CONSULT-OPD", "op_consulting_charge": fee,
			"inpatient_visit_charge_item": "CONSULT-IPD", "inpatient_visit_charge": 800}).name
	log("Physiotherapy Gym, Emergency Bays 1-3, Dr Meera Pillai (physio), Dr Sameer Khan (A&E)")
	return extra


def seed_availability(dr):
	if frappe.db.exists("Practitioner Availability", {"scope": dr["vikram"]}):
		return
	for scope, kind, reason, start, end, s, e, note, repeat in (
		(dr["vikram"], "Unavailable", "Training", d(5), d(5), "09:00:00", "13:00:00", "ACLS refresher course", "Never"),
		(dr["ananya"], "Unavailable", "Time Off", d(20), d(24), "09:00:00", "13:00:00", "Annual leave", "Never"),
		(dr["fatima"], "Available", None, d(1), d(60), "16:00:00", "18:00:00", "Evening ANC clinic (Saturdays)", "Weekly"),
	):
		pa = frappe.get_doc({"doctype": "Practitioner Availability", "type": kind, "reason": reason,
			"scope_type": "Healthcare Practitioner", "scope": scope, "start_date": start, "end_date": end,
			"start_time": s, "end_time": e, "note": note, "repeat": repeat, "saturday": 1 if repeat == "Weekly" else 0,
			"status": "Active"})
		pa.insert(ignore_permissions=True)
		pa.submit()
	log("Dr Vikram training day, Dr Ananya leave, Dr Fatima weekly evening ANC clinic")


# ─── phase 2 transactional stories ──────────────────────────────────────────


def story_fee_validity(dr, units):
	frappe.db.set_value("Healthcare Practitioner", dr["fatima"], {"enable_free_follow_ups": 1, "max_visits": 2, "valid_days": 30})
	pt = make_patient("Meena", "Joshi", "Female", "1997-06-08", blood_group="B Positive", mobile="9876500009",
		marital_status="Married", occupation="Software engineer")
	if has_story(pt):
		return
	a = book(pt, dr["fatima"], units["opd3"], "Consultation", d(-10), "11:00:00")
	encounter(a, ["Irregular Periods", "Weight Gain"], ["Polycystic Ovary Syndrome"], labs=[get_lab("FBS")],
		comment="Oligomenorrhoea x 1 yr, acne. USG pelvis advised. Lifestyle modification.")
	invoice(pt, pay=True)
	book(pt, dr["fatima"], units["opd3"], "Follow-up", d(-2), "11:00:00", notes="USG review — free follow-up")
	log("Meena Joshi — paid consult with Dr Fatima, free follow-up covered by Fee Validity")


def get_lab(key):
	return {"FBS": "Fasting Blood Sugar", "CBC": "Complete Blood Count", "LIPID": "Lipid Profile",
	        "HBA1C": "HbA1c", "CREAT": "Serum Creatinine"}[key]


def seed_insurance():
	receivable = f"Debtors - {ABBR}"
	expense = first_leaf_account(COMPANY, root_type="Expense", account_type=["in", ["", None]]) or \
		first_leaf_account(COMPANY, root_type="Expense")
	payor = get_or_create("Insurance Payor", {"insurance_payor_name": "Star Health Insurance"}, {
		"insurance_claim_credit_days": 30, "code_system": "ICD-10", "website": "https://example.org/star-health",
		"claims_receivable_accounts": [{"company": COMPANY, "account": receivable}],
		"rejected_claims_expense_accounts": [{"company": COMPANY, "account": expense}],
	}).name
	if not frappe.db.exists("Insurance Payor Contract", {"insurance_payor": payor, "docstatus": 1}):
		c = frappe.get_doc({"doctype": "Insurance Payor Contract", "insurance_payor": payor, "company": COMPANY,
			"start_date": d(-180), "end_date": d(185), "is_active": 1, "default_price_list": PRICE_LIST})
		c.insert(ignore_permissions=True)
		c.submit()
	plan = get_or_create("Insurance Payor Eligibility Plan", {"insurance_plan_name": "Star Family Health Optima"},
		{"insurance_payor": payor, "is_active": 1, "price_list": PRICE_LIST}).name
	for dt, dn, cover in (
		("Appointment Type", "Consultation", 80), ("Appointment Type", "Follow-up", 80),
		("Lab Test Template", "Lipid Profile", 90), ("Lab Test Template", "HbA1c", 90),
		("Clinical Procedure Template", "ECG (12-lead)", 100), ("Healthcare Service Unit Type", "General Ward Bed", 75),
	):
		if not frappe.db.exists("Item Insurance Eligibility", {"insurance_plan": plan, "template_dt": dt, "template_dn": dn}):
			frappe.get_doc({"doctype": "Item Insurance Eligibility", "insurance_plan": plan, "eligibility_for": "Service",
				"template_dt": dt, "template_dn": dn, "mode_of_approval": "Automatic", "coverage": cover,
				"valid_from": d(-180), "is_active": 1}).insert(ignore_permissions=True)
	log(f"payor {payor}, active contract, plan {plan}, 6 item eligibilities")
	return payor, plan


def story_insured_patient(dr, units, payor, plan, procs):
	pt = make_patient("Anil", "Verma", "Male", "1975-01-19", blood_group="A Positive", mobile="9876500010",
		marital_status="Married", occupation="Civil engineer", medical_history="Dyslipidaemia")
	if has_story(pt):
		return
	pol = frappe.get_doc({"doctype": "Patient Insurance Policy", "patient": pt, "insurance_payor": payor,
		"insurance_plan": plan, "policy_number": "SHI-2026-447120", "policy_expiry_date": d(270)})
	pol.insert(ignore_permissions=True)
	pol.submit()
	a = book(pt, dr["vikram"], units["opd2"], "Consultation", d(-6), "10:00:00")
	a.db_set("insurance_policy", pol.name)
	a.reload()
	a.make_insurance_coverage()
	a.reload()
	enc = frappe.get_doc({
		"doctype": "Patient Encounter", "appointment": a.name, "appointment_type": a.appointment_type,
		"patient": pt, "practitioner": a.practitioner, "company": COMPANY, "insurance_policy": pol.name,
		"encounter_date": a.appointment_date, "encounter_time": a.appointment_time,
		"symptoms": [{"complaint": "Chest Pain"}], "diagnosis": [{"diagnosis": "Stable Angina (suspected)"}],
		"lab_test_prescription": [{"lab_test_code": "Lipid Profile"}, {"lab_test_code": "HbA1c"}],
		"procedure_prescription": [{"procedure": procs["ECG"]}],
		"encounter_comment": "Cashless OPD under Star Health. Atypical chest pain; rule out IHD.",
	})
	enc.insert(ignore_permissions=True)
	enc.submit()
	lab_results(enc, {"Lipid Profile": {"Total Cholesterol": "248", "LDL Cholesterol": "168", "HDL Cholesterol": "36", "Triglycerides": "190"},
	                  "HbA1c": "5.9"})
	complete_procedure(enc)
	inv = invoice(pt)
	claim = frappe.new_doc("Insurance Claim")
	claim.update({"insurance_payor": payor, "customer": frappe.db.get_value("Insurance Payor", payor, "customer"),
		"company": COMPANY, "posting_date": TODAY, "due_date": d(30), "mode_of_payment": "Cash",
		"patient": pt, "insurance_policy": pol.name, "from_date": d(-30), "to_date": TODAY,
		"posting_date_based_on": "Insurance Coverage", "status": "Draft"})
	claim.get_coverages()
	claim.insert(ignore_permissions=True)
	claim.submit()
	cov = frappe.db.count("Patient Insurance Coverage", {"patient": pt})
	log(f"Anil Verma — insured OPD: policy, {cov} coverages, invoice {inv.name if inv else '-'}, claim {claim.name}")


def order_observations(sr_name):
	"""Equivalent of service_request.make_observation without its early return, which skips every
	other order of an encounter once one of them already has a Diagnostic Report."""
	from healthcare.healthcare.doctype.service_request.service_request import (
		create_sample_collection, insert_diagnostic_report, insert_observation_and_sample_collection)

	sr = frappe.get_doc("Service Request", sr_name)
	patient = frappe.get_doc("Patient", sr.patient)
	template = frappe.get_doc("Observation Template", sr.template_dn)
	sc = create_sample_collection(patient, sr)
	sc, diag_report_required = insert_observation_and_sample_collection(sr, patient.name, template, sc)
	if sc and sc.get("observation_sample_collection"):
		sc.save(ignore_permissions=True)
	report = frappe.db.exists("Diagnostic Report", {"docname": sr.order_group})
	if diag_report_required and not report:
		insert_diagnostic_report(sr, sc.name if sc and sc.name else None)
		report = frappe.db.exists("Diagnostic Report", {"docname": sr.order_group})
	if report and sc and sc.name and not frappe.db.get_value("Diagnostic Report", report, "sample_collection"):
		frappe.db.set_value("Diagnostic Report", report, "sample_collection", sc.name)
	return sc.name if sc and sc.name else None


def story_observations(dr, units):
	pt = make_patient("Neha", "Gupta", "Female", "1988-10-02", blood_group="O Positive", mobile="9876500011",
		marital_status="Married", occupation="Teacher")
	if frappe.db.exists("Sample Collection", {"patient": pt}):
		return
	from healthcare.healthcare.doctype.observation.observation import record_observation_result
	from healthcare.healthcare.doctype.sample_collection.sample_collection import create_observation

	enc_name = frappe.db.get_value("Patient Encounter", {"patient": pt, "docstatus": 1})
	if not enc_name:
		a = book(pt, dr["ananya"], units["opd1"], "Consultation", d(-4), "12:00:00")
		enc = frappe.get_doc({
			"doctype": "Patient Encounter", "appointment": a.name, "appointment_type": a.appointment_type,
			"patient": pt, "practitioner": a.practitioner, "company": COMPANY,
			"encounter_date": a.appointment_date, "encounter_time": a.appointment_time,
			"symptoms": [{"complaint": c} for c in ("Weight Gain", "Cold Intolerance", "Fatigue")],
			"diagnosis": [{"diagnosis": "Hypothyroidism"}],
			"lab_test_prescription": [{"observation_template": "Thyroid Profile"}, {"observation_template": "Chest X-Ray PA View"}],
			"encounter_comment": "Dry skin, puffy face, delayed ankle reflexes. TFT ordered; CXR as baseline (chronic cough).",
		})
		enc.insert(ignore_permissions=True)
		enc.submit()
		enc_name = enc.name

	sc_name = None
	for sr in frappe.get_all("Service Request", {"order_group": enc_name, "template_dt": "Observation Template"}, pluck="name"):
		if frappe.db.exists("Observation", {"service_request": sr}):
			continue
		sc_name = order_observations(sr) or sc_name
	if sc_name:
		sc = frappe.get_doc("Sample Collection", sc_name)
		sc.collection_point = units["lab"]
		sc.collected_time = f"{d(-4)} 12:30:00"
		sc.save(ignore_permissions=True)
		for row in sc.observation_sample_collection:
			r = row.as_dict()
			create_observation(json.dumps([r], default=str), sc.name, r.get("component_observations"), r.name)
		sc.reload()
		sc.submit()

	results = {"TSH": "9.8", "Free T4": "0.62", "Free T3": "2.1",
	           "Chest X-Ray PA View": "Normal cardiac silhouette. Clear lung fields. No pleural effusion."}
	payload = []
	for obs in frappe.get_all("Observation", {"patient": pt, "docstatus": 0}, ["name", "observation_template"]):
		if obs.observation_template in results:
			payload.append({"observation": obs.name, "result": results[obs.observation_template],
			                "interpretation": "No active cardiopulmonary disease" if obs.observation_template == "Chest X-Ray PA View" else None,
			                "note": "Raised TSH with low FT4 — primary hypothyroidism" if obs.observation_template == "TSH" else None})
	if payload:
		record_observation_result(json.dumps(payload))
	for obs in frappe.get_all("Observation", {"patient": pt, "docstatus": 0}, ["name"], order_by="parent_observation desc"):
		doc = frappe.get_doc("Observation", obs.name)
		doc.status = "Final"
		doc.submit()
	report = frappe.db.get_value("Diagnostic Report", {"patient": pt})
	if report:
		from healthcare.healthcare.doctype.diagnostic_report.diagnostic_report import set_observation_status
		set_observation_status(report)
	if not frappe.db.exists("Sales Invoice", {"patient": pt, "docstatus": 1}):
		invoice(pt, pay=True)
	n = frappe.db.count("Observation", {"patient": pt})
	log(f"Neha Gupta — TFT + CXR: Sample Collection {sc_name}, {n} observations, Diagnostic Report {report}")


def story_therapy(dr, units):
	pt = make_patient("Suresh", "Reddy", "Male", "1961-08-25", blood_group="B Positive", mobile="9876500012",
		marital_status="Married", occupation="Retired bank manager", medical_history="Bilateral knee OA (R > L)")
	if has_story(pt):
		return
	from healthcare.healthcare.doctype.therapy_plan.therapy_plan import make_therapy_session

	a = book(pt, dr["meera"], units["physio"], "Consultation", d(-14), "09:30:00")
	enc = frappe.get_doc({
		"doctype": "Patient Encounter", "appointment": a.name, "appointment_type": a.appointment_type,
		"patient": pt, "practitioner": a.practitioner, "company": COMPANY,
		"encounter_date": a.appointment_date, "encounter_time": a.appointment_time,
		"symptoms": [{"complaint": "Joint Pain"}, {"complaint": "Knee Swelling"}],
		"diagnosis": [{"diagnosis": "Primary Osteoarthritis of Knee"}],
		"therapies": [{"therapy_type": "Knee Physiotherapy", "no_of_sessions": 6}],
		"encounter_comment": "Kellgren-Lawrence grade 2 right knee. Flexion 95°. 6 sessions of supervised physio.",
	})
	enc.insert(ignore_permissions=True)
	enc.submit()
	plan = frappe.db.get_value("Therapy Plan", {"patient": pt})
	scores = [
		{"Pain (VAS)": 3, "Knee Flexion Range": 4, "Quadriceps Strength": 4, "Swelling": 5, "Walking Tolerance": 3},
		{"Pain (VAS)": 5, "Knee Flexion Range": 6, "Quadriceps Strength": 5, "Swelling": 6, "Walking Tolerance": 5},
		{"Pain (VAS)": 7, "Knee Flexion Range": 7, "Quadriceps Strength": 7, "Swelling": 8, "Walking Tolerance": 7},
	]
	for i, day in enumerate((-12, -9, -6)):
		ts = frappe.get_doc(make_therapy_session(pt, "Knee Physiotherapy", COMPANY, therapy_plan=plan))
		ts.start_date = d(day)
		ts.start_time = "10:00:00"
		ts.service_unit = units["physio"]
		ts.location = "Center"
		for e in ts.exercises:
			e.counts_completed = int((e.counts_target or 10) * (0.5 + 0.2 * i))
		ts.insert(ignore_permissions=True)
		ts.submit()
		if i in (0, 2):
			pa = frappe.get_doc({"doctype": "Patient Assessment", "patient": pt, "therapy_session": ts.name,
				"assessment_template": "Knee Function Assessment", "company": COMPANY,
				"healthcare_practitioner": dr["meera"], "assessment_datetime": f"{d(day)} 10:45:00",
				"scale_min": 0, "scale_max": 10,
				"assessment_description": "Baseline" if i == 0 else "Progress after 3 sessions",
				"assessment_sheet": [{"parameter": k, "score": str(v), "time": "10:45:00"} for k, v in scores[i].items()]})
			pa.insert(ignore_permissions=True)
			pa.submit()
	invoice(pt)
	log(f"Suresh Reddy — Therapy Plan {plan}: 3 of 6 sessions done, 2 assessments (score 19 → 36 / 50)")


def receive_drug_stock(meds):
	if frappe.db.exists("Stock Entry", {"remarks": "Seed: pharmacy opening stock", "docstatus": 1}):
		return
	se = frappe.new_doc("Stock Entry")
	se.update({"stock_entry_type": "Material Receipt", "company": COMPANY, "posting_date": d(-30),
	           "set_posting_time": 1, "remarks": "Seed: pharmacy opening stock"})
	for m in meds.values():
		row = m.linked_items[0]
		frappe.db.set_value("Item", row.item_code, "is_stock_item", 1)
		se.append("items", {"item_code": row.item_code, "qty": 500, "t_warehouse": f"Stores - {ABBR}",
		                    "basic_rate": round((row.rate or 1) * 0.6, 2), "uom": "Nos", "conversion_factor": 1})
	se.insert(ignore_permissions=True)
	se.submit()


def story_inpatient_care(p, dr, meds):
	ip = frappe.db.get_value("Inpatient Record", {"patient": p["rajesh"], "status": "Admitted"})
	if not ip or frappe.db.exists("Inpatient Medication Order", {"inpatient_record": ip}):
		return
	ip_doc = frappe.get_doc("Inpatient Record", ip)
	receive_drug_stock(meds)
	amox, para = meds["Amoxicillin"].linked_items[0].item_code, meds["Paracetamol"].linked_items[0].item_code
	entries = []
	for day in (-2, -1, 0):
		for t in ("08:00:00", "14:00:00", "20:00:00"):
			entries.append({"drug": amox, "dosage": 1, "dosage_form": "Capsule", "date": d(day), "time": t,
			                "instructions": "After food"})
		for t in ("08:00:00", "20:00:00"):
			entries.append({"drug": para, "dosage": 1, "dosage_form": "Tablet", "date": d(day), "time": t,
			                "instructions": "If temp > 100°F"})
	imo = frappe.get_doc({"doctype": "Inpatient Medication Order", "patient": p["rajesh"], "inpatient_record": ip,
		"company": COMPANY, "practitioner": dr["vikram"], "start_date": d(-2), "medication_orders": entries})
	imo.insert(ignore_permissions=True)
	imo.submit()
	ime = frappe.get_doc({"doctype": "Inpatient Medication Entry", "company": COMPANY, "posting_date": d(-1),
		"patient": p["rajesh"], "from_date": d(-2), "to_date": d(-1), "update_stock": 1, "warehouse": f"Stores - {ABBR}"})
	ime.get_medication_orders()
	ime.insert(ignore_permissions=True)
	ime.submit()

	from healthcare.healthcare.doctype.nursing_task.nursing_task import NursingTask
	NursingTask.create_nursing_tasks_from_template("General Ward Admission", ip_doc, start_time=f"{d(-2)} 14:00:00")
	for i, task in enumerate(frappe.get_all("Nursing Task", {"patient": p["rajesh"]}, ["name", "activity"], order_by="creation")):
		doc = frappe.get_doc("Nursing Task", task.name)
		if doc.docstatus == 0:
			doc.submit()
			doc.reload()
		if i < 3 and not doc.task_doctype:
			doc.status = "Completed"
			doc.task_start_time = f"{d(-2)} 14:{10 + i * 10}:00"
			doc.save(ignore_permissions=True)

	for day, kind, text in (
		(-2, "Nursing Note", "Admitted 14:00. SpO2 90% RA → 95% on 2 L O2. IV cannula 20G left forearm. Febrile 102.4°F."),
		(-1, "Progress Note", "Day 2 IV/oral amoxicillin. Fever spikes reducing (max 100.8°F). Crepitations persist R base."),
		(0, "Progress Note", "Afebrile 18 h. SpO2 96% on RA. Plan: continue oral antibiotics, discharge in 48 h if stable."),
	):
		frappe.get_doc({"doctype": "Clinical Note", "patient": p["rajesh"], "clinical_note_type": kind,
			"posting_date": f"{d(day)} 18:00:00", "note": f"<p>{text}</p>"}).insert(ignore_permissions=True)
	log(f"Rajesh Patel — {len(entries)} IP medication orders, 2 days dispensed from stock, nursing tasks, 3 clinical notes")


def story_discharge(dr, units, meds):
	pt = make_patient("Gopal", "Krishnan", "Male", "1964-12-11", blood_group="A Positive", mobile="9876500013",
		marital_status="Married", occupation="Auto-rickshaw driver")
	if has_story(pt):
		return
	from healthcare.healthcare.doctype.inpatient_record.inpatient_record import (
		admit_patient, discharge_patient, schedule_discharge, schedule_inpatient)

	a = book(pt, dr["ananya"], units["opd1"], "Consultation", d(-8), "12:30:00")
	enc = encounter(a, ["Fever", "Body Ache", "Headache", "Vomiting"], ["Dengue Fever"],
		meds=[meds["Paracetamol"]], labs=["Complete Blood Count"],
		comment="Day 4 of fever, NS1 positive outside. Platelets 62k. Admit for monitoring & fluids.")
	lab_results(enc, {"Complete Blood Count": {"Haemoglobin": "15.2", "Total WBC Count": "2900", "Platelet Count": "0.62", "RBC Count": "5.1"},
	                  "Complete Blood Count:comment": "Leucopenia, thrombocytopenia, haemoconcentration"})
	schedule_inpatient({"patient": pt, "admission_encounter": enc.name, "referring_practitioner": dr["ananya"],
		"admission_ordered_for": d(-8), "admission_service_unit_type": "General Ward Bed", "expected_length_of_stay": 5,
		"admission_instruction": "IV fluids 100 ml/h, platelet count 12-hourly, watch for warning signs",
		"medical_department": "General Medicine", "primary_practitioner": dr["ananya"],
		"admission_nursing_checklist_template": "General Ward Admission",
		"discharge_nursing_checklist_template": "Ward Discharge"})
	ip = frappe.get_last_doc("Inpatient Record", filters={"patient": pt})
	ip.db_set("scheduled_date", d(-8))
	ip.reload()
	admit_patient(ip, units["bed2"], f"{d(-8)} 14:00:00", expected_discharge=d(-3))

	dis_enc = frappe.get_doc({"doctype": "Patient Encounter", "appointment_type": "Follow-up", "patient": pt,
		"practitioner": dr["ananya"], "company": COMPANY, "encounter_date": d(-3), "encounter_time": "10:00:00",
		"inpatient_record": ip.name, "diagnosis": [{"diagnosis": "Dengue Fever"}],
		"encounter_comment": "Platelets 1.45 lakh, afebrile 48 h, tolerating orally. Fit for discharge."})
	dis_enc.insert(ignore_permissions=True)
	dis_enc.submit()
	schedule_discharge(json.dumps({"patient": pt, "discharge_encounter": dis_enc.name,
		"discharge_practitioner": dr["ananya"], "discharge_ordered_date": d(-3), "followup_date": d(4),
		"discharge_instructions": "Oral fluids 3 L/day, avoid NSAIDs, return if bleeding or abdominal pain",
		"discharge_note": "Recovered from dengue without warning signs"}, default=str))
	ds = frappe.get_doc({"doctype": "Discharge Summary", "inpatient_record": ip.name, "patient": pt,
		"discharge_practitioner": dr["ananya"], "primary_practitioner": dr["ananya"], "posting_date": d(-3),
		"company": COMPANY, "status": "Approved", "followup_date": d(4),
		"chief_complaint": [{"complaint": c} for c in ("Fever", "Body Ache", "Vomiting")],
		"diagnosis": [{"diagnosis": "Dengue Fever"}],
		"physical_examination": "<p>Febrile, mild dehydration, no bleeding manifestations, no hepatomegaly.</p>",
		"treatment_done": "<p>IV crystalloids 48 h, paracetamol, serial platelet monitoring (nadir 0.48 lakh on day 2).</p>",
		"advice_on_discharge": "<p>Oral fluids, paracetamol SOS, avoid aspirin/ibuprofen. Repeat CBC on follow-up.</p>",
		"diet_adviced": "<p>Soft, high-fluid diet; papaya/coconut water as tolerated.</p>"})
	ds.insert(ignore_permissions=True)
	ds.submit()
	# Repeat CBC ordered during the stay must be resulted before discharge.
	from healthcare.healthcare.doctype.service_request.service_request import make_lab_test
	for sr in frappe.get_all("Service Request", {"inpatient_record": ip.name, "docstatus": 1,
	                                            "status": ["!=", "completed-Request Status"]}, ["name", "template_dt"]):
		if sr.template_dt != "Lab Test Template":
			continue
		lt = make_lab_test(sr.name)
		lt.insert(ignore_permissions=True)
		for row in lt.normal_test_items:
			row.result_value = {"Haemoglobin": "13.9", "Total WBC Count": "4600", "Platelet Count": "1.45", "RBC Count": "4.8"}.get(row.lab_test_event or row.lab_test_name, "")
		lt.lab_test_comment = "Platelets recovering"
		lt.save(ignore_permissions=True)
		lt.submit()
	# Discharge bills the stay only after check-out, so allow it momentarily, then bill everything.
	# Also bypass the pending-orders check: Health v16 compares Service Request status against
	# "Completed" while it stores "completed-Request Status", so completed orders still block discharge.
	flags = ("allow_discharge_despite_unbilled_services", "allow_discharge_despite_pending_healthcare_services")
	for flag in flags:
		frappe.db.set_single_value("Healthcare Settings", flag, 1)
	try:
		discharge_patient(frappe.get_doc("Inpatient Record", ip.name))
	finally:
		for flag in flags:
			frappe.db.set_single_value("Healthcare Settings", flag, 0)
	frappe.db.set_value("Inpatient Record", ip.name, "discharge_datetime", f"{d(-3)} 13:00:00")
	frappe.db.set_value("Inpatient Occupancy", {"parent": ip.name}, "check_out", f"{d(-3)} 13:00:00")
	inv = invoice(pt, pay=True)
	log(f"Gopal Krishnan — dengue: admitted 5 days, Discharge Summary {ds.name}, discharged, final bill {inv.name if inv else '-'} paid")


def story_emergency(p, dr, extra, units):
	if frappe.db.count("Emergency Record") >= 3:
		return
	from frappe.utils import now_datetime

	def er(arrival, mode, complaint, triage, bed, vitals, disposition=None, notes=None, **kw):
		rec = frappe.get_doc({"doctype": "Emergency Record", "company": COMPANY, "arrival_datetime": arrival,
			"arrival_mode": mode, "chief_complaint": complaint, "medical_department": "Accident And Emergency Care",
			"triage_practitioner": extra["sameer"], "attending_practitioner": extra["sameer"], **kw})
		rec.insert(ignore_permissions=True)
		rec.record_triage(triage)
		rec.assign_bed(bed, arrival)
		rec.add_vital_signs([{"template": t, "result": v} for t, v in vitals.items()])
		rec.reload()
		if disposition:
			rec.set_disposition(disposition, notes)
		return rec

	er(f"{d(-30)} 22:15:00", "Walk-in", "Acute wheeze and breathlessness since evening, unable to speak full sentences",
		"Urgent", units["er2"], {"Pulse": "128", "Respiratory Rate": "34", "SpO2": "91", "Temperature": "99.1"},
		"Discharged", "Responded to 3 salbutamol nebs + oral prednisolone. SpO2 97%. Discharged with inhaler technique.",
		patient=p["arjun"])
	er(f"{d(0)} 06:40:00", "Ambulance", "Road traffic accident — two-wheeler, unhelmeted, brief LOC, scalp laceration",
		"Emergency", units["er1"], {"Pulse": "118", "BP Systolic": "100", "BP Diastolic": "64", "SpO2": "95", "Respiratory Rate": "22"},
		"Admitted", "GCS 14. CT head: small contusion, no midline shift. Admit for neuro observation.",
		patient_description="Unidentified male, ~30 y, RTA", gender="Male",
		referring_practitioner=None)
	er(f"{d(0)} 08:20:00", "Walk-in", "Severe occipital headache and blurred vision since morning",
		"Urgent", units["er3"], {"BP Systolic": "196", "BP Diastolic": "114", "Pulse": "96", "SpO2": "98"},
		patient=p["kavita"])
	log("3 emergency records: Arjun asthma (discharged), unidentified RTA (admitted), Kavita hypertensive urgency (in treatment)")


def story_treatment_counselling(dr, units, meds):
	pt = make_patient("Harish", "Chandra", "Male", "1952-03-03", blood_group="O Negative", mobile="9876500014",
		marital_status="Married", occupation="Retired", medical_history="COPD, ex-smoker 40 pack-years")
	if has_story(pt):
		return
	from healthcare.healthcare.doctype.inpatient_record.inpatient_record import schedule_inpatient

	a = book(pt, dr["vikram"], units["opd2"], "Consultation", d(-1), "11:30:00")
	enc = encounter(a, ["Fever", "Cough", "Breathlessness"], ["Community-acquired Pneumonia"],
		labs=["Complete Blood Count"], comment="CURB-65 = 2. Needs admission; family counselled on cost estimate.")
	schedule_inpatient({"patient": pt, "admission_encounter": enc.name, "referring_practitioner": dr["vikram"],
		"admission_ordered_for": d(0), "admission_service_unit_type": "General Ward Bed", "expected_length_of_stay": 5,
		"medical_department": "General Medicine", "primary_practitioner": dr["vikram"],
		"admission_instruction": "IV antibiotics, nebulisation, O2 to keep SpO2 > 92%",
		"treatment_plan_template": "Community-acquired Pneumonia — Inpatient"})
	tc = frappe.db.get_value("Treatment Counselling", {"patient": pt})
	if tc:
		doc = frappe.get_doc("Treatment Counselling", tc)
		if doc.docstatus == 0:
			doc.submit()
	log(f"Harish Chandra — admission advised; Treatment Counselling {tc} (cost estimate) awaiting deposit")


def seed_clinical_notes(p):
	if frappe.db.exists("Clinical Note", {"patient": p["ramesh"]}):
		return
	for patient, day, kind, text in (
		(p["ramesh"], -28, "Counselling Note", "Diet counselling: 1800 kcal, low GI, 30 min brisk walk daily. Smoking cessation advised — patient willing; NRT discussed."),
		(p["sunita"], -14, "Counselling Note", "Birth preparedness, danger signs of pregnancy, iron-folic acid compliance explained in Hindi."),
		(p["irfan"], 0, "Procedure Note", "12-lead ECG: sinus rhythm 92/min, 1 mm horizontal ST depression V4-V6. TMT requested."),
	):
		frappe.get_doc({"doctype": "Clinical Note", "patient": patient, "clinical_note_type": kind,
			"posting_date": f"{d(day)} 13:00:00", "note": f"<p>{text}</p>"}).insert(ignore_permissions=True)
	log("3 OPD clinical notes")


def seed_phase2(p, dr, units, meds, labs, procs, schedule):
	section("Terminology (ICD-10 / LOINC / SNOMED)", seed_terminology)
	section("Clinical vocabulary & masters", seed_more_vocab)
	section("Sample containers", seed_samples)
	section("Observation templates", seed_observation_templates)
	extra = section("Extra service units & staff", seed_extra_units_and_staff, units, schedule) or {}
	section("Rehabilitation masters", seed_rehab_masters, units)
	section("Nursing masters", seed_nursing_masters)
	section("Medication safety & allergies", seed_safety, p)
	section("Treatment plan templates", seed_treatment_plans, dr, labs, procs, meds)
	section("Practitioner availability", seed_availability, dr)
	ins = section("Insurance masters", seed_insurance)
	all_dr = {**dr, **extra}
	print("Phase 2 stories")
	section("  fee validity", story_fee_validity, all_dr, units)
	if ins:
		section("  insurance", story_insured_patient, all_dr, units, ins[0], ins[1], procs)
	section("  observations & diagnostics", story_observations, all_dr, units)
	section("  therapy", story_therapy, all_dr, units)
	section("  inpatient care", story_inpatient_care, p, all_dr, meds)
	section("  discharge", story_discharge, all_dr, units, meds)
	section("  emergency", story_emergency, p, all_dr, extra, units)
	section("  treatment counselling", story_treatment_counselling, all_dr, units, meds)
	section("  clinical notes", seed_clinical_notes, p)


def run(company=None):
	global COMPANY, ABBR, TODAY
	COMPANY = company or frappe.defaults.get_global_default("company")
	if not COMPANY or not frappe.db.exists("Company", COMPANY):
		frappe.throw(f"Company {COMPANY!r} not found; pass company=<name>")
	ABBR = frappe.db.get_value("Company", COMPANY, "abbr")
	TODAY = getdate(today())

	seed_basics()
	units = seed_service_units()
	schedule = seed_schedule()
	dr = seed_practitioners(units, schedule)
	seed_appointment_types()
	seed_clinical_vocab()
	meds = seed_medications()
	labs = seed_lab_templates()
	procs = seed_procedures()
	patients = seed_patients()
	frappe.db.commit()
	seed_stories(patients, dr, units, meds, labs, procs)
	frappe.db.commit()
	seed_phase2(patients, dr, units, meds, labs, procs, schedule)
	frappe.db.commit()
	print("Done.")
