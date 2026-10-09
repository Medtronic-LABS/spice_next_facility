"""Site-local corrections to frappe_theme endpoints, wired through `override_whitelisted_methods`."""

import frappe
from frappe_theme.dt_api import get_chart_data as frappe_theme_get_chart_data


@frappe.whitelist()
def get_chart_data(type, details, report=None, doctype=None, docname=None, filters=None):
	"""frappe_theme's chart component always sends `filters` (an empty `{}` when nothing is set). On a
	form that is not a frappe_theme dashboard, `Chart.chart_doc_type` passes that JSON string straight to
	`list.extend`, splicing its characters into the query ("Expected 'and' or 'or' operator, found …").
	Drop empty filters before delegating; anything with content goes through unchanged."""
	parsed = frappe.parse_json(filters) if isinstance(filters, str) else filters
	return frappe_theme_get_chart_data(type, details, report, doctype, docname, parsed or None)
