"""The Healthcare desk navigation: Dock -> Menu (Sidebar) -> items.

Pure data. `apply.py` writes it; change menus here, then `bench migrate` (or reinstall) applies it.
"""

HOST_APP = "healthcare"
HOST_MODULE = "Healthcare"


def link(label, link_to, link_type="DocType", icon=None):
	return {"type": "Link", "label": label, "link_type": link_type, "link_to": link_to, "icon": icon}


def doc(name, icon=None):
	return link(name, name, "DocType", icon)


def report(name):
	return link(name, name, "Report")


def submenu(label, icon, children):
	return [
		{"type": "Section Break", "label": label, "link_type": "DocType", "icon": icon,
		 "indent": 1, "collapsible": 1, "keep_closed": 1},
		*[{**c, "child": 1, "icon": None} for c in children],
	]


# Menu title -> (dock icon, items). The menu named after the module ("Healthcare") is the one Desk
# falls back to for any Healthcare document opened outside the dock (search, links).
MENUS = {
	"Healthcare": ("house", [
		link("Home", "Healthcare", "Workspace", "house"),
		link("Dashboard", "Healthcare", "Dashboard", "chart-column"),
		doc("Patient", "user-round"),
		doc("Patient Appointment", "calendar-days"),
		doc("Patient Encounter", "stethoscope"),
		doc("Inpatient Record", "bed-double"),
		doc("Emergency Record", "siren"),
		link("Patient History", "patient_history", "Page", "file-heart"),
		link("Patient Progress", "patient-progress", "Page", "activity"),
		*submenu("Reports", "sheet", [
			report("Patient Appointment Analytics"), report("Diagnosis Trends"), report("Lab Test Report"),
			report("Emergency Triage Queue"), report("Inpatient Medication Orders"),
			report("Planned vs Actual Therapy Session"), report("Medication Item wise Sales"),
		]),
	]),
	"Outpatient": ("stethoscope", [
		link("Home", "Outpatient", "Workspace", "house"),
		doc("Patient", "user-round"),
		doc("Patient Appointment", "calendar-days"),
		doc("Patient Encounter", "stethoscope"),
		doc("Vital Signs", "heart-pulse"),
		doc("Service Request", "clipboard-list"),
		doc("Medication Request", "pill"),
		doc("Clinical Procedure", "syringe"),
		*submenu("Patient Records", "file-heart", [
			doc("Patient Allergy"), doc("Clinical Note"), doc("Patient Medical Record"), doc("Fee Validity"),
			link("Patient History", "patient_history", "Page"),
		]),
		*submenu("Scheduling", "calendar-clock", [
			doc("Healthcare Practitioner"), doc("Practitioner Schedule"), doc("Practitioner Availability"),
			doc("Appointment Type"),
		]),
		*submenu("Reports", "sheet", [report("Patient Appointment Analytics"), report("Diagnosis Trends")]),
	]),
	"Emergency": ("siren", [
		link("Home", "Emergency", "Workspace", "house"),
		doc("Emergency Record", "ambulance"),
		doc("Patient", "user-round"),
		doc("Vital Signs", "heart-pulse"),
		doc("Observation", "activity"),
		*submenu("Reports", "sheet", [report("Emergency Triage Queue")]),
		*submenu("Setup", "database", [doc("Triage Level"), doc("Healthcare Service Unit")]),
	]),
	"Inpatient": ("bed-double", [
		link("Home", "Inpatient", "Workspace", "house"),
		doc("Inpatient Record", "bed-double"),
		doc("Inpatient Medication Order", "clipboard-plus"),
		doc("Inpatient Medication Entry", "pill-bottle"),
		doc("Nursing Task", "list-checks"),
		doc("Clinical Note", "notebook-pen"),
		doc("Discharge Summary", "scroll-text"),
		doc("Treatment Counselling", "hand-coins"),
		*submenu("Care Plans", "clipboard-check", [
			doc("Treatment Plan Template"), doc("Nursing Checklist Template"), doc("Healthcare Activity"),
		]),
		*submenu("Reports", "sheet", [report("Inpatient Medication Orders")]),
	]),
	"Diagnostics": ("microscope", [
		link("Home", "Diagnostics", "Workspace", "house"),
		doc("Lab Test", "flask-conical"),
		doc("Sample Collection", "test-tube"),
		doc("Observation", "activity"),
		doc("Diagnostic Report", "file-text"),
		doc("Specimen", "test-tubes"),
		doc("Clinical Procedure", "syringe"),
		*submenu("Lab Setup", "database", [
			doc("Lab Test Template"), doc("Observation Template"), doc("Lab Test Sample"), doc("Sample Type"),
			doc("Lab Test UOM"),
		]),
		*submenu("Microbiology", "microscope", [doc("Organism"), doc("Antibiotic"), doc("Sensitivity")]),
		*submenu("Procedure Setup", "syringe", [doc("Clinical Procedure Template")]),
		*submenu("Reports", "sheet", [report("Lab Test Report")]),
	]),
	"Pharmacy": ("pill", [
		doc("Medication Request", "clipboard-list"),
		doc("Inpatient Medication Entry", "pill-bottle"),
		doc("Medication", "pill"),
		doc("Stock Entry", "layers"),
		doc("Medication Alert Log", "badge-alert"),
		*submenu("Drug Master", "book-open", [
			doc("Medication Class"), doc("Medication Interaction"), doc("Allergy"), doc("Dosage Form"),
			doc("Prescription Dosage"), doc("Prescription Duration"),
		]),
		*submenu("Reports", "sheet", [report("Medication Item wise Sales"), report("Stock Balance")]),
	]),
	"Rehabilitation": ("dumbbell", [
		link("Home", "Rehabilitation", "Workspace", "house"),
		doc("Therapy Plan", "clipboard-list"),
		doc("Therapy Session", "dumbbell"),
		doc("Patient Assessment", "clipboard-check"),
		*submenu("Therapy Setup", "database", [
			doc("Therapy Type"), doc("Therapy Plan Template"), doc("Exercise Type"), doc("Body Part"),
			doc("Exercise Difficulty Level"),
		]),
		*submenu("Assessment Setup", "list-checks", [
			doc("Patient Assessment Template"), doc("Patient Assessment Parameter"),
		]),
		*submenu("Reports", "sheet", [report("Planned vs Actual Therapy Session")]),
	]),
	"Billing and Insurance": ("shield-check", [
		link("Home", "Insurance", "Workspace", "house"),
		doc("Sales Invoice", "receipt-text"),
		doc("Payment Entry", "wallet"),
		doc("Patient Insurance Policy", "shield-plus"),
		doc("Patient Insurance Coverage", "shield-check"),
		doc("Insurance Claim", "circle-dollar-sign"),
		*submenu("Payors", "building-2", [
			doc("Insurance Payor"), doc("Insurance Payor Contract"), doc("Insurance Payor Eligibility Plan"),
			doc("Item Insurance Eligibility"),
		]),
		*submenu("Reports", "sheet", [report("Accounts Receivable"), report("Sales Register")]),
	]),
	"Healthcare Setup": ("settings", [
		link("Home", "Setup", "Workspace", "house"),
		doc("Healthcare Settings", "settings"),
		doc("Healthcare Practitioner", "briefcase-medical"),
		doc("Medical Department", "hospital"),
		doc("Healthcare Service Unit", "building-2"),
		doc("Healthcare Service Unit Type", "layers"),
		doc("Appointment Type", "calendar-days"),
		*submenu("Clinical Vocabulary", "tags", [
			doc("Complaint"), doc("Diagnosis"), doc("Clinical Note Type"), doc("Patient Care Type"),
			doc("Service Request Category"), doc("Service Request Reason"),
		]),
		*submenu("Terminology", "book-open", [doc("Code System"), doc("Code Value"), doc("Code Value Set")]),
		*submenu("Settings", "database", [doc("Patient History Settings"), doc("ABDM Settings")]),
	]),
}


# Area dashboard workspace -> the menu (and module) it opens in. "Healthcare" stays in its own module.
WORKSPACE_MENU = {
	"Outpatient": "Outpatient", "Emergency": "Emergency", "Inpatient": "Inpatient",
	"Diagnostics": "Diagnostics", "Rehabilitation": "Rehabilitation",
	"Insurance": "Billing and Insurance", "Setup": "Healthcare Setup",
}
# Which menu a Healthcare doctype opens in when reached from search or a link: the first area menu
# in this order that lists it. The overview menu claims nothing, it is a launcher.
OWNERSHIP_ORDER = ["Outpatient", "Emergency", "Inpatient", "Diagnostics", "Pharmacy", "Rehabilitation",
                   "Billing and Insurance", "Healthcare Setup"]
