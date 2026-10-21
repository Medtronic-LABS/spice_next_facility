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

# Each item in the list will be shown as an app in the apps page.
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/spice_facility/images/spice_facility.svg",
		"title": app_title,
		"route": app_home,
		"has_permission": "frappe.permissions.check_app_permission",
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
	"/assets/spice_facility/js/form_ux_strings.js",
	"/assets/spice_facility/js/form_ux.js",
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

before_install = "spice_facility.setup.install.before_install"
after_install = "spice_facility.setup.install.after_install"
# Re-applied on every migrate: a Health update can re-import its workspaces and settings.
after_migrate = ["spice_facility.setup.install.after_migrate"]
before_uninstall = "spice_facility.setup.install.before_uninstall"
