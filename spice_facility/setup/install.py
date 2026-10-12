import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from spice_facility.ai.config import CLOUD_OPT_OUT_FIELD
from spice_facility.desk_navigation.apply import apply_navigation, remove_navigation
from spice_facility.form_ux.apply import apply_form_ux, remove_form_ux
from spice_facility.setup.fixes import apply_fixes

AI_SETTINGS = "SPICE AI Settings"
AI_DEFAULT_ROLES = ("Physician", "Nursing User", "Healthcare Administrator", "System Manager")

APP = "spice_facility"


def before_install():
	release_hand_made_modules()


def after_install():
	apply_fixes()
	apply_navigation()
	apply_form_ux()
	fill_ai_settings_defaults()
	ensure_ai_patient_fields()
	seed_ai_roles()


def after_migrate():
	apply_fixes()
	apply_navigation()
	apply_form_ux()
	fill_ai_settings_defaults()
	ensure_ai_patient_fields()
	seed_ai_roles()


def before_uninstall():
	# Uninstalling deletes every record in this app's modules, which would include Health's
	# dashboards while they sit in them.
	remove_form_ux()
	remove_navigation()


def release_hand_made_modules():
	"""Drop custom Module Defs a site made for these menus before this app existed.

	Left in place, the installer renames them out of the way ("Outpatient (Custom)") and keeps them
	as clutter. Tearing the navigation down first unlinks every workspace and sidebar from them, so
	they can go; `after_install` rebuilds the navigation on this app's own modules."""
	hand_made = [m for m in frappe.get_module_list(APP) if frappe.db.get_value("Module Def", m, "custom")]
	if not hand_made:
		return
	remove_navigation()
	for module in hand_made:
		frappe.delete_doc("Module Def", module, ignore_permissions=True, force=True)


def fill_ai_settings_defaults():
	"""Give settings fields added in an update their declared defaults (e.g. a new provider's API URL).

	A Single gets its defaults only while it has never been saved; afterwards a new field reads as empty.
	Only fields that have no stored value are filled, so a site's own choices are never overridden. Runs
	before anything saves the settings, which would store the new fields as empty."""
	stored = frappe.db.get_singles_dict(AI_SETTINGS)
	for df in frappe.get_meta(AI_SETTINGS).fields:
		if df.default is not None and stored.get(df.fieldname) is None:
			frappe.db.set_single_value(AI_SETTINGS, df.fieldname, df.default)
	frappe.clear_document_cache(AI_SETTINGS, AI_SETTINGS)


def ensure_ai_patient_fields():
	"""The patient's own choice to keep their records away from cloud AI models."""
	create_custom_fields({"Patient": [{
		"fieldname": CLOUD_OPT_OUT_FIELD, "fieldtype": "Check", "label": "Keep out of cloud AI",
		"insert_after": "report_preference", "default": "0",
		"description": "The patient asked that their records never be sent to cloud AI models (AI summaries then "
		               "need the local model).",
	}]}, update=True)


def seed_ai_roles():
	"""Give the clinical roles access to AI features the first time; never override a site's choice."""
	settings = frappe.get_single(AI_SETTINGS)
	if settings.allowed_roles:
		return
	for role in AI_DEFAULT_ROLES:
		if frappe.db.exists("Role", role):
			settings.append("allowed_roles", {"role": role})
	settings.flags.ignore_permissions = True
	settings.save()
