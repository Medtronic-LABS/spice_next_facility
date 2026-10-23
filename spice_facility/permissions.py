"""Who may open the spice_facility app from the apps screen.

`frappe.permissions.check_app_permission` -- the generic default most apps' `add_to_apps_screen`
entries use (see uhis/hooks.py) -- only returns True for System Manager. That's the right gate for
an admin-only app, but spice_facility is meant for day-to-day facility staff (reception, doctors,
finance), none of whom hold System Manager. Gate on facility roles instead.
"""

import frappe

#: Any role here is enough to open the app icon. Kept in one place so a new facility role (a
#: fixture in this app, or a stock Healthcare role like Physician/Nursing User) only has to be
#: added here to also unlock the apps-screen icon.
FACILITY_ROLES = frozenset(
	{
		"System Manager",
		"Healthcare Administrator",
		"Receptionist",
		"Finance Staff",
		"Physician",
		"Nursing User",
		"Laboratory User",
		"LabTest Approver",
	}
)


def has_app_permission():
	return bool(FACILITY_ROLES.intersection(frappe.get_roles(frappe.session.user)))
