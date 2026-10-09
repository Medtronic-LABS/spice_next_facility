app_name = "spice_facility"
app_title = "SPICE Facility"
app_publisher = "Medtronic Labs"
app_description = "SPICE facility layer on Frappe Health: desk navigation, defaults, fixes and demo data."
app_email = "admin@medtroniclabs.org"
app_license = "gpl-3.0"

# Frappe Health ships as the `healthcare` app (github.com/frappe/health) and needs ERPNext.
required_apps = ["erpnext", "healthcare"]

app_include_js = ["/assets/spice_facility/js/full_width_default.js"]

before_install = "spice_facility.setup.install.before_install"
after_install = "spice_facility.setup.install.after_install"
# Re-applied on every migrate: a Health update can re-import its workspaces and settings.
after_migrate = ["spice_facility.setup.install.after_migrate"]
before_uninstall = "spice_facility.setup.install.before_uninstall"
