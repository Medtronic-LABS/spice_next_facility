"""Write `spec.py` to the site: an Overview tab of frappe_theme `sva_ft` blocks on Health forms.

Everything written is site content owned by this app, so Health is never edited:
  - Custom Fields `sf_*` (Overview tab, its sections/columns/HTML fields, and a "Details" tab in front
    of the original fields), plus a `field_order` Property Setter that puts the Overview tab first.
    The order is recomputed from the live meta every run, so fields Health adds later keep their place.
  - one `sva_ft` Property Setter per HTML field (compact JSON, `is_system_generated` so Customize Form's
    "Reset to Defaults" keeps it)
  - the Number Cards, Dashboard Charts and Custom HTML Blocks the blocks point at

Idempotent; `sf_*` artefacts no longer in the spec are removed.
"""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import delete_property_setter, make_property_setter

from spice_facility.form_ux.connections import apply_connections, connection_problems, remove_connections
from spice_facility.form_ux.spec import FORM_MODULE, FORMS, HTML_BLOCKS

PREFIX = "sf_"
SVA_FT = "sva_ft"
FIELD_ORDER = "field_order"
OVERVIEW_TAB = "sf_overview_tab"
DETAILS_TAB = "sf_details_tab"
CONNECTIONS_TAB = "sf_connections_tab"
BLOCK_SCRIPT = 'spice_facility.form_ux.mount(root_element, "{key}");'
BLOCK_HTML = '<div class="sf-block" data-block="{key}"></div>'


def compact(value):
	"""JSON exactly as frappe_theme matches it (`apis/meta.py` LIKE '%"link_doctype":"X"%'), at every level."""
	return json.dumps(value, separators=(",", ":"))


class FormUxSpecError(frappe.ValidationError):
	pass


def apply_form_ux():
	problems = spec_problems() + connection_problems()
	if problems:
		raise FormUxSpecError("Form UX spec does not match this site: " + "; ".join(problems))

	upsert_html_blocks()
	for doctype, blocks in FORMS.items():
		module = FORM_MODULE[doctype]
		for block in iter_blocks(blocks):
			if block["kind"] == "card":
				upsert_number_card(block, module)
			elif block["kind"] == "chart":
				upsert_dashboard_chart(block, module)
		apply_doctype(doctype, blocks)
	remove_stale_records()
	apply_connections()


def remove_stale_records():
	"""Drop the `SF …` cards and charts this app created that the spec no longer uses."""
	wanted = {block["name"] for blocks in FORMS.values() for block in iter_blocks(blocks) if "name" in block}
	for doctype in ("Number Card", "Dashboard Chart"):
		for name in frappe.get_all(doctype, {"name": ["like", "SF %"], "is_standard": 0}, pluck="name"):
			if name not in wanted:
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def remove_form_ux():
	remove_connections()
	for doctype in FORMS:
		delete_property_setter(doctype, FIELD_ORDER)
		for name in frappe.get_all("Property Setter", {"doc_type": doctype, "property": SVA_FT,
		                                               "field_name": ["like", f"{PREFIX}%"]}, pluck="name"):
			frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
		for name in frappe.get_all("Custom Field", {"dt": doctype, "fieldname": ["like", f"{PREFIX}%"]}, pluck="name"):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
		frappe.clear_cache(doctype=doctype)
	for blocks in FORMS.values():
		for block in iter_blocks(blocks):
			doctype = {"card": "Number Card", "chart": "Dashboard Chart"}.get(block["kind"])
			if doctype and frappe.db.exists(doctype, block["name"]):
				frappe.delete_doc(doctype, block["name"], ignore_permissions=True, force=True)
	for name in HTML_BLOCKS:
		if frappe.db.exists("Custom HTML Block", name):
			frappe.delete_doc("Custom HTML Block", name, ignore_permissions=True, force=True)


# ─── validation ─────────────────────────────────────────────────────────────


def iter_blocks(blocks):
	for block in blocks:
		yield from block["blocks"] if block["kind"] == "row" else [block]


def has_field(doctype, fieldname):
	return fieldname == "name" or bool(frappe.get_meta(doctype).get_field(fieldname))


def scoping_field(source_doctype, parent_doctype):
	"""The field frappe_theme will scope a card/chart on, or None if it cannot scope it cleanly."""
	meta = frappe.get_meta(source_doctype)
	direct = next((f.fieldname for f in meta.fields if f.fieldtype == "Link" and f.options == parent_doctype
	               and f.fieldname != "amended_from"), None)
	reference_pair = any(f.fieldtype == "Link" and f.options == "DocType" for f in meta.fields) and any(
		f.fieldtype == "Dynamic Link" for f in meta.fields)
	return None if reference_pair else direct


def spec_problems():
	problems = []
	for name in HTML_BLOCKS:
		if not name.startswith("SF "):
			problems.append(f"HTML block {name!r} must be named 'SF …'")
	for doctype, blocks in FORMS.items():
		if not frappe.db.exists("DocType", doctype):
			problems.append(f"{doctype}: doctype missing")
			continue
		for block in iter_blocks(blocks):
			where = f"{doctype}.{block['fieldname']}"
			kind = block["kind"]
			if not block["fieldname"].startswith(PREFIX):
				problems.append(f"{where}: fieldname must start with {PREFIX}")
			if kind == "table":
				problems += table_problems(doctype, block, where)
			elif kind in ("card", "chart"):
				source = block["document_type"]
				if scoping_field(source, doctype) != block["scope"]:
					problems.append(f"{where}: {source} would not scope on {block['scope']}")
				if block["kind"] == "chart" and not has_field(source, block["based_on"]):
					problems.append(f"{where}: {source}.{block['based_on']} missing")
			elif kind == "html" and block["block"] not in HTML_BLOCKS:
				problems.append(f"{where}: unknown HTML block {block['block']}")
	return problems


def table_problems(doctype, block, where):
	problems = []
	child = block["link_doctype"]
	if not frappe.db.exists("DocType", child):
		return [f"{where}: {child} missing"]
	extra = block["extra"]
	needed = {
		"Direct": [(child, block["link_fieldname"])],
		"Indirect": [(doctype, extra.get("local_field")), (child, extra.get("foreign_field"))],
		"Referenced": [(child, extra.get("dt_reference_field")), (child, extra.get("dn_reference_field"))],
	}.get(block["connection"])
	if needed is None:
		return [f"{where}: unknown connection {block['connection']}"]
	for dt, field in needed + [(child, c["fieldname"]) for c in block["columns"]] + [
		(child, cond[0]) for cond in block["conditions"]
	]:
		if not field or not has_field(dt, field):
			problems.append(f"{where}: {dt}.{field} missing")
	return problems


# ─── records the blocks point at ────────────────────────────────────────────


def upsert(doctype, name, values, name_field):
	doc = frappe.get_doc(doctype, name) if frappe.db.exists(doctype, name) else frappe.new_doc(doctype)
	doc.update({name_field: name, **values})
	doc.flags.ignore_permissions = True
	doc.save() if not doc.is_new() else doc.insert(set_name=name)
	return doc.name


def filters_json(doctype, filters):
	return json.dumps([[doctype, f, op, v] for f, op, v in filters])


def upsert_html_blocks():
	for name, key in HTML_BLOCKS.items():
		upsert("Custom HTML Block", name, {
			"html": BLOCK_HTML.format(key=key), "script": BLOCK_SCRIPT.format(key=key), "style": "", "private": 0,
		}, "name")


def upsert_number_card(block, module):
	upsert("Number Card", block["name"], {
		"type": "Document Type", "function": "Count", "document_type": block["document_type"],
		"filters_json": filters_json(block["document_type"], block["filters"]), "is_public": 1,
		"is_standard": 0, "module": module, "show_percentage_stats": 0,
	}, "label")


def upsert_dashboard_chart(block, module):
	values = {
		"chart_type": block["chart_type"], "document_type": block["document_type"], "type": block["type"],
		"filters_json": filters_json(block["document_type"], block["filters"]), "is_public": 1,
		"is_standard": 0, "module": module, "timespan": block["timespan"], "time_interval": block["interval"],
	}
	values.update({"based_on": block["based_on"], "timeseries": 1})
	upsert("Dashboard Chart", block["name"], values, "chart_name")


# ─── form layout ────────────────────────────────────────────────────────────


def sva_ft_value(block):
	kind = block["kind"]
	if kind == "html":
		conf = {"property_type": "Custom HTML Block", "html_block": block["block"]}
	elif kind == "card":
		conf = {"property_type": "Number Card", "number_card": block["name"], "label": block["label"],
		        "show_full_number": 1}
	elif kind == "chart":
		conf = {"property_type": "Dashboard Chart", "chart": block["name"], "label": block["label"]}
	else:
		connection = block["connection"]
		conf = {
			"property_type": f"DocType ({connection})", "connection_type": connection,
			"link_doctype": block["link_doctype"], "link_fieldname": block["link_fieldname"],
			"title": block["label"], "crud_permissions": compact(block["crud"]),
			"listview_settings": compact([{**c, "inline_edit": 0, "sticky": 0, "wrap": 0} for c in block["columns"]]),
			# Opening rows in their own form also stops frappe_theme freezing that doctype's form.
			"redirect_to_main_form": 1, "disable_workflow": 1, "allow_export": 1,
			**block["extra"],
		}
		if connection == "Referenced":
			conf["referenced_link_doctype"] = block["link_doctype"]
		if block["conditions"]:
			conf.update({"extend_condition": 1, "extended_condition": compact(
				[[block["link_doctype"], f, op, v] for f, op, v in block["conditions"]])})
	return compact(conf)


def layout_fields(blocks):
	"""Custom field dicts for the Overview tab, in display order."""
	fields = [{"fieldname": OVERVIEW_TAB, "fieldtype": "Tab Break", "label": "Overview"}]
	for i, block in enumerate(blocks):
		members = block["blocks"] if block["kind"] == "row" else [block]
		label = "" if members[0]["kind"] == "table" or block["kind"] == "row" else members[0]["label"]
		fields.append({"fieldname": f"{PREFIX}sec_{i}", "fieldtype": "Section Break", "label": label})
		for j, member in enumerate(members):
			if j:
				fields.append({"fieldname": f"{PREFIX}col_{i}_{j}", "fieldtype": "Column Break"})
			fields.append({"fieldname": member["fieldname"], "fieldtype": "HTML", "label": member["label"]})
	fields.append({"fieldname": DETAILS_TAB, "fieldtype": "Tab Break", "label": "Details"})
	return fields


def apply_doctype(doctype, blocks):
	fields = layout_fields(blocks)
	# Frappe draws the Connections panel in the first tab unless a tab claims it; Overview is first now,
	# so a form without its own dashboard tab gets one at the end.
	has_dashboard_tab = any(f.fieldtype == "Tab Break" and f.show_dashboard and not f.fieldname.startswith(PREFIX)
	                        for f in frappe.get_meta(doctype).fields)
	trailing = [] if has_dashboard_tab else [
		{"fieldname": CONNECTIONS_TAB, "fieldtype": "Tab Break", "label": "Connections", "show_dashboard": 1}]
	wanted = {f["fieldname"] for f in fields + trailing}

	for name, fieldname in frappe.get_all("Custom Field", {"dt": doctype, "fieldname": ["like", f"{PREFIX}%"]},
	                                      ["name", "fieldname"], as_list=True):
		if fieldname not in wanted:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

	delete_property_setter(doctype, FIELD_ORDER)
	frappe.clear_cache(doctype=doctype)
	native = [f.fieldname for f in frappe.get_meta(doctype).fields if not f.fieldname.startswith(PREFIX)]

	previous = native[-1]
	for field in fields + trailing:
		field["insert_after"] = previous
		previous = field["fieldname"]
	create_custom_fields({doctype: fields + trailing}, ignore_validate=bool(duplicate_native_fields(native)), update=True)

	order = [f["fieldname"] for f in fields] + native + [f["fieldname"] for f in trailing]
	make_property_setter(doctype, None, FIELD_ORDER, json.dumps(order),
	                     "Data", for_doctype=True, validate_fields_for_doctype=False, is_system_generated=True)

	for block in iter_blocks(blocks):
		set_sva_ft(doctype, block["fieldname"], sva_ft_value(block))
	for name in frappe.get_all("Property Setter", {"doc_type": doctype, "property": SVA_FT,
	                                               "field_name": ["like", f"{PREFIX}%"]}, ["name", "field_name"],
	                           as_list=True):
		if name[1] not in wanted:
			frappe.delete_doc("Property Setter", name[0], ignore_permissions=True, force=True)
	frappe.clear_cache(doctype=doctype)


def duplicate_native_fields(native):
	"""Fieldnames the doctype's own JSON declares twice. Health v16's Insurance Claim ships
	`mode_of_payment_section` and `column_break_133` twice, which makes Frappe reject every custom field
	on it; only then is field validation skipped, so it comes back once Health fixes its JSON."""
	seen, dupes = set(), set()
	for fieldname in native:
		(dupes if fieldname in seen else seen).add(fieldname)
	return dupes


def set_sva_ft(doctype, fieldname, value):
	name = f"{doctype}-{fieldname}-{SVA_FT}"
	if frappe.db.get_value("Property Setter", name, "value") == value:
		return
	make_property_setter(doctype, fieldname, SVA_FT, value, "JSON", validate_fields_for_doctype=False,
	                     is_system_generated=True)
