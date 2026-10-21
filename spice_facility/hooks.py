app_name = "spice_facility"
app_title = "SPICE Facility"
app_publisher = "Medtronic Labs"
app_description = "SPICE facility layer on Frappe Health: desk navigation, defaults, fixes and demo data."
app_email = "admin@medtroniclabs.org"
app_license = "gpl-3.0"

# This app owns its Sidebars/Dock (desk_navigation/menus.py's HOST_APP), so the rail built from
# MENUS there is this app's own rail, not Health's -- app_landing_route's dock-fallback
# (frappe/public/js/frappe/ui/sidebar/sidebar.js) would resolve through it. Routing explicitly
# instead, the same way healthcare's and erpnext's own hooks do, so the icon's landing page is a
# single deterministic value rather than "whichever dock row sorts first": "Healthcare" is the
# Workspace desk_navigation's own "Home" link treats as the front door.
app_home = "/desk/healthcare"

# Each item in the list will be shown as an app in the apps page. has_permission is this app's own
# (not the generic frappe.permissions.check_app_permission, which only passes System Manager --
# see permissions.py for why that's wrong for a staff app with Receptionist/Physician/Finance
# Staff roles).
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/spice_facility/images/spice_facility.svg",
		"title": app_title,
		"route": app_home,
		"has_permission": "spice_facility.permissions.has_app_permission",
	}
]

# Desk/login page logo -- see frappe.core.doctype.navbar_settings.get_app_logo's
# fallback chain (Website Settings.app_logo -> Navbar Settings.app_logo -> this
# hook).
app_logo_url = "/assets/spice_facility/images/spice_facility.svg"

# Frappe Health ships as the `healthcare` app (github.com/frappe/health) and needs ERPNext;
# frappe_theme renders the `sva_ft` blocks on the Overview tab of Health forms (form_ux/).
required_apps = ["erpnext", "healthcare", "frappe_theme"]

app_include_css = [
	"/assets/spice_facility/css/full_width.css",
	"/assets/spice_facility/css/ai.css",
	"/assets/spice_facility/css/patient_form.css",
]

app_include_js = [
	"/assets/spice_facility/js/full_width_default.js",
	"/assets/spice_facility/js/hide_sidebar_on_load.js",
	"/assets/spice_facility/js/form_ux_strings.js",
	"/assets/spice_facility/js/form_ux.js",
	"/assets/spice_facility/js/modern_connections.js",
	"/assets/spice_facility/js/ai_strings.js",
	"/assets/spice_facility/js/ai_summary.js",
	# Ask Data: shared helpers, then the parts the view mixes in, then the view itself
	"/assets/spice_facility/js/ask_data/shared.js",
	"/assets/spice_facility/js/ask_data/answer_card.js",
	"/assets/spice_facility/js/ask_data/chat_list.js",
	"/assets/spice_facility/js/ask_data/composer.js",
	"/assets/spice_facility/js/ask_data/ask_data_view.js",
	"/assets/spice_facility/js/ask_data/floating_chat.js",
]

# frappe_theme sends an empty `{}` filter that breaks every form-scoped Dashboard Chart; see overrides/.
override_whitelisted_methods = {
	"frappe_theme.dt_api.get_chart_data": "spice_facility.overrides.frappe_theme.get_chart_data",
}

# Consumed from frappe_theme (third-party; not modified) -- My Theme's existing hide_side_bar /
# hide_form_comment / hide_fields_comment toggles. See fixtures/my_theme.json for why it's a full
# snapshot, not just the three fields.
#
# "Receptionist" and "Finance Staff" (role.json/custom_docperm.json) are purpose-built roles
# scoped to exactly the Reception & Check-in / Finance & Billing menus' own item sets. Frappe's
# Sidebar resolver (frappe/desk/doctype/sidebar/sidebar.py:resolve_sidebar) already filters every
# menu item by DocType permission and drops a whole menu when nothing in it is permitted -- so a
# role scoped this tightly automatically sees only its own menu in the dock, no custom nav code
# needed. "Doctor" reuses Healthcare's own stock "Physician" role instead of a new one here: its
# existing DocPerms are already scoped almost identically to Doctor - OPDs.
#
# Filtered to just these two roles' own rows -- Role/Custom DocPerm both have hundreds of
# site-wide records, so a bare doctype name here would make `bench export-fixtures` dump every
# role and permission on the site into this app's fixtures, not just these two.
fixtures = [
	"My Theme",
	{"dt": "Role", "filters": [["name", "in", ["Receptionist", "Finance Staff"]]]},
	{
		"dt": "Custom DocPerm",
		"filters": [["role", "in", ["Receptionist", "Finance Staff"]]],
	},
]

# Patient-portal nav for the demo flow -- Appointments is already a Patient Portal SPA tab; these
# two just surface healthcare's own existing web forms (personal-details, prescription) that
# otherwise have no portal menu entry. Matches healthcare's own standard_portal_menu_items pattern
# exactly (hooks.py's Patient Portal entry) -- not a fixture, Portal Settings.sync_menu() merges
# this hook's items on migrate/install (frappe/migrate.py, frappe/installer.py).
standard_portal_menu_items = [
	{"title": "Profile", "route": "/personal-details", "reference_doctype": "Patient", "role": "Patient"},
	{
		"title": "Medicine Orders",
		"route": "/prescription",
		"reference_doctype": "Patient Encounter",
		"role": "Patient",
	},
]

before_install = "spice_facility.setup.install.before_install"
after_install = "spice_facility.setup.install.after_install"
# Re-applied on every migrate: a Health update can re-import its workspaces and settings.
after_migrate = ["spice_facility.setup.install.after_migrate"]
before_uninstall = "spice_facility.setup.install.before_uninstall"
