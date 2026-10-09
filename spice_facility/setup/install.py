import frappe

from spice_facility.desk_navigation.apply import apply_navigation, remove_navigation
from spice_facility.setup.fixes import apply_fixes

APP = "spice_facility"


def before_install():
	adopt_module_defs()


def after_install():
	apply_fixes()
	apply_navigation()


def after_migrate():
	apply_fixes()
	apply_navigation()


def before_uninstall():
	# Uninstalling deletes every record in this app's modules, which would include Health's
	# dashboards while they sit in them.
	remove_navigation()


def adopt_module_defs():
	"""Take over area modules a site created by hand before this app existed, so installing does
	not trip over an existing Module Def of the same name owned by another app."""
	for module in frappe.get_module_list(APP):
		if frappe.db.exists("Module Def", module):
			frappe.db.set_value("Module Def", module, {"app_name": APP, "custom": 0}, update_modified=False)
