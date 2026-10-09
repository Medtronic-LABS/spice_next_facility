import frappe

from spice_facility.desk_navigation.apply import apply_navigation, remove_navigation
from spice_facility.form_ux.apply import apply_form_ux, remove_form_ux
from spice_facility.setup.fixes import apply_fixes

AI_DEFAULT_ROLES = ("Physician", "Nursing User", "Healthcare Administrator", "System Manager")

APP = "spice_facility"


def before_install():
	release_hand_made_modules()


def after_install():
	apply_fixes()
	apply_navigation()
	apply_form_ux()
	seed_ai_roles()


def after_migrate():
	apply_fixes()
	apply_navigation()
	apply_form_ux()
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


def seed_ai_roles():
	"""Give the clinical roles access to AI features the first time; never override a site's choice."""
	settings = frappe.get_single("SPICE AI Settings")
	if settings.allowed_roles:
		return
	for role in AI_DEFAULT_ROLES:
		if frappe.db.exists("Role", role):
			settings.append("allowed_roles", {"role": role})
	settings.flags.ignore_permissions = True
	settings.save()
