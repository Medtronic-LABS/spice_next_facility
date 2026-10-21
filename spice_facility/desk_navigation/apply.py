"""Write `menus.py` to the site as Frappe 16 desk navigation.

Everything written is site content, so the Health app is never edited:
  - each area's dashboard `Workspace` is moved into this app's module of the same name. Frappe 16
    resolves the menu a workspace opens in by module, and Health ships one module, so without this
    every workspace opens in whichever Health menu sorts first.
  - one `Sidebar` (standard=0) per menu. Non-standard rows are not orphans, so migrate keeps them.
    `is_default_module` marks the menu that owns each Health doctype.
  - the site layer of this app's own `Dock` (standard=0, user="") listing the menus -- see
    menus.HOST_APP for why it is this app's name, not Health's.

Idempotent: every menu and the dock are rebuilt from the spec on each call.
"""

import frappe

from spice_facility.desk_navigation.menus import (
	HOST_APP,
	HOST_MODULE,
	MENU_LABELS,
	MENUS,
	OWNERSHIP_ORDER,
	WORKSPACE_MENU,
)


class UnresolvedMenuLinkError(frappe.ValidationError):
	pass


def apply_navigation():
	missing = missing_targets()
	if missing:
		raise UnresolvedMenuLinkError("Unresolved menu links: " + "; ".join(missing))

	move_workspaces(WORKSPACE_MENU)
	owner = doctype_owners()
	# Sidebars refuse writes outside developer mode unless the system is installing or migrating.
	previous = frappe.flags.in_install
	frappe.flags.in_install = frappe.flags.in_install or "spice_facility"
	try:
		entries = [(upsert_sidebar(title, icon, items, owner), icon) for title, (icon, items) in MENUS.items()]
		upsert_dock(entries)
	finally:
		frappe.flags.in_install = previous
	frappe.clear_cache()


def remove_navigation():
	"""Undo `apply_navigation`, putting the dashboards back before this app's modules are dropped."""
	move_workspaces({workspace: HOST_MODULE for workspace in WORKSPACE_MENU})
	dock = site_dock_name()
	if dock:
		frappe.delete_doc("Dock", dock, ignore_permissions=True, force=True)
	for title in MENUS:
		name = sidebar_name(title)
		if frappe.db.exists("Sidebar", name):
			frappe.delete_doc("Sidebar", name, ignore_permissions=True, force=True)
	frappe.clear_cache()


def menu_module(title):
	return HOST_MODULE if title == HOST_MODULE else title


def sidebar_name(title):
	"""Sidebar's autoname is `field:title`, so its docname IS whatever this returns -- never the bare
	MENUS/module key once MENU_LABELS gives that key a different display title. Every lookup against
	the Sidebar doctype (exists/get_doc/delete_doc) must go through this, not the key directly, or an
	idempotent re-run (apply_navigation fires from both after_install and after_migrate) looks up the
	old name, finds nothing, and tries to insert a second row under the name the first run already
	took -- a DuplicateEntryError, not a no-op."""
	return MENU_LABELS.get(title, title)


def missing_targets():
	"""Every link must resolve, so a typo fails the install instead of becoming a dead entry."""
	return [
		f"{title}: {item['link_type']} {item['link_to']}"
		for title, (_icon, items) in MENUS.items()
		for item in items
		if item["type"] == "Link" and not frappe.db.exists(item["link_type"], item["link_to"])
	]


def move_workspaces(mapping):
	for workspace, module in mapping.items():
		if frappe.db.exists("Workspace", workspace) and frappe.db.get_value("Workspace", workspace, "module") != module:
			# update_modified=False keeps Health's file from looking newer than the row, so a plain
			# migrate does not re-import over it. A Health release that changes the file still does,
			# which is why this also runs after every migrate.
			frappe.db.set_value("Workspace", workspace, "module", menu_module(module), update_modified=False)


def doctype_owners():
	"""The area menu a Health doctype opens in from search or a link: the first in `OWNERSHIP_ORDER`
	that lists it. Doctypes of other apps (Sales Invoice, Stock Entry) keep their own menus."""
	owner = {}
	for title in OWNERSHIP_ORDER:
		for item in MENUS[title][1]:
			if item["type"] == "Link" and item["link_type"] == "DocType":
				if frappe.db.get_value("DocType", item["link_to"], "module") == HOST_MODULE:
					owner.setdefault(item["link_to"], title)
	return owner


def upsert_sidebar(title, icon, items, owner):
	name = sidebar_name(title)
	sidebar = frappe.get_doc("Sidebar", name) if frappe.db.exists("Sidebar", name) else frappe.new_doc("Sidebar")
	# `title` (the MENUS/module key) stays the `module` field for routing; the Sidebar's own docname
	# and displayed title are `name` (sidebar_name(title)), which can read differently -- see
	# MENU_LABELS in menus.py for why.
	sidebar.update({
		"title": name,
		"app": HOST_APP,
		"module": menu_module(title),
		"header_icon": icon,
		"standard": 0,
	})
	rows = []
	for item in items:
		row = {key: value for key, value in item.items() if value is not None}
		if row.get("link_type") == "DocType" and owner.get(row.get("link_to")) == title:
			row["is_default_module"] = 1
		rows.append(row)
	sidebar.set("items", rows)
	sidebar.save(ignore_permissions=True)
	return sidebar.name


def site_dock_name():
	return frappe.db.get_value("Dock", {"app": HOST_APP, "standard": 0, "user": ""})


def upsert_dock(entries):
	name = site_dock_name()
	dock = frappe.get_doc("Dock", name) if name else frappe.new_doc("Dock")
	dock.update({"app": HOST_APP, "standard": 0, "user": ""})
	dock.set(
		"items",
		[
			# `sidebar` here is already the Sidebar's resolved docname (upsert_sidebar's return value,
			# i.e. sidebar_name(title)) -- not a MENUS key, so no further MENU_LABELS lookup applies.
			{
				"link_type": "Sidebar",
				"link_to": sidebar,
				"title": sidebar,
				"icon": icon,
				"added": 1,
				"hidden": 0,
			}
			for sidebar, icon in entries
		],
	)
	dock.save(ignore_permissions=True)
	return dock.name
