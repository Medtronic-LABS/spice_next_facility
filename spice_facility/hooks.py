app_name = "spice_facility"
app_title = "SPICE Facility"
app_publisher = "Medtronic Labs"
app_description = "SPICE facility layer on Frappe Health: desk navigation, defaults, fixes and demo data."
app_email = "admin@medtroniclabs.org"
app_license = "gpl-3.0"

# Frappe Health ships as the `healthcare` app (github.com/frappe/health) and needs ERPNext;
# frappe_theme renders the `sva_ft` blocks on the Overview tab of Health forms (form_ux/).
required_apps = ["erpnext", "healthcare", "frappe_theme"]

app_include_css = ["/assets/spice_facility/css/full_width.css", "/assets/spice_facility/css/ai.css"]

app_include_js = [
	"/assets/spice_facility/js/full_width_default.js",
	"/assets/spice_facility/js/form_ux_strings.js",
	"/assets/spice_facility/js/form_ux.js",
	"/assets/spice_facility/js/ai_strings.js",
	"/assets/spice_facility/js/ai_summary.js",
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
