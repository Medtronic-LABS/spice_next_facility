// Replaces the stock Frappe "Connections" tab's heatmap + badge-list rendering with embedded
// tables of the actual linked records, using frappe_theme's existing `frappe.ui.SvaDataTable`
// component (the same one form_ux's Overview tab already uses -- see
// frappe_theme/public/js/overwrite_form.bundle.js:initializeSvaDataTable for the reference
// instantiation this mirrors). frappe_theme is third-party here and is not modified; this only
// *consumes* its already-global `frappe.ui.SvaDataTable` class.
//
// Scoped to the 5 doctypes spice_facility/form_ux/connections.py already curates correct
// `DocType Link` data for -- that curated data (which doctypes show as connections, and their
// link fieldname) is the single source of truth this reads from `frm.dashboard.data.transactions`;
// nothing here changes which doctypes appear, only how each one renders.
(function () {
	const TARGET_DOCTYPES = [
		"Patient",
		"Inpatient Record",
		"Healthcare Practitioner",
		"Emergency Record",
		"Patient Insurance Policy",
	];

	function buildColumns(doctype) {
		const meta = frappe.get_meta(doctype);
		if (!meta) return [];
		// SvaDataTable re-filters whatever columns it's given down to `in_list_view` fields
		// (sva_datatable.bundle.js), so an "ID" column spliced in without that flag would be
		// silently dropped -- it already always renders the row name/title itself, so this just
		// passes the doctype's own list-view fields, same as a stock list view would show.
		return meta.fields.filter((f) => f.in_list_view).slice(0, 5);
	}

	function destroyPreviousTables(frm) {
		(frm._modern_connection_tables || []).forEach((instance) => {
			try {
				instance.cleanup && instance.cleanup();
				instance.destroy && instance.destroy();
				instance.datatable && instance.datatable.destroy();
			} catch (e) {
				// A table from a previous document failing to tear down cleanly must not block
				// rendering the current one.
				console.error("modern_connections: cleanup failed", e);
			}
		});
		frm._modern_connection_tables = [];
	}

	function renderConnectionTable(frm, doctype, fieldname) {
		const wrapper = document.createElement("div");

		const section = $(`
			<div class="modern-connection" style="margin-bottom: 20px;">
				<div class="modern-connection-title" style="font-weight:600; cursor:pointer; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
					<span class="modern-connection-toggle text-muted">−</span>
					<span>${frappe.utils.escape_html(__(doctype))}</span>
				</div>
				<div class="modern-connection-body"></div>
			</div>
		`);
		section.find(".modern-connection-body").append(wrapper);
		section.find(".modern-connection-title").on("click", function () {
			const body = section.find(".modern-connection-body");
			body.toggle();
			section.find(".modern-connection-toggle").text(body.is(":hidden") ? "+" : "−");
		});

		const instance = new frappe.ui.SvaDataTable({
			label: __(doctype),
			wrapper,
			doctype,
			frm,
			connection: {
				connection_type: "Direct",
				link_fieldname: fieldname,
				html_field: `modern_connection_${doctype}`,
				// A JSON-string-encoded array -- SvaDataTable does
				// `JSON.parse(connection.crud_permissions ?? "[]")` (sva_datatable.bundle.js),
				// not a live frappe.has_permission check, so this must be set explicitly or
				// every table silently renders as "no permission" regardless of the real user's
				// access. Read-only: this view is for seeing linked records, not editing them.
				crud_permissions: JSON.stringify(["read"]),
			},
			childLinks: [],
			columns: buildColumns(doctype),
			options: { serialNumberColumn: false, editable: false, pageLimit: 5 },
		});

		frm._modern_connection_tables.push(instance);
		return section;
	}

	function renderModernConnections(frm) {
		const dashboard = frm.dashboard;
		if (!dashboard) return;

		// Heatmap never applies to a clinical record.
		dashboard.heatmap_area && dashboard.heatmap_area.hide();

		destroyPreviousTables(frm);

		const container = dashboard.transactions_area;
		if (!container) return;
		container.empty();

		const transactions = (dashboard.data && dashboard.data.transactions) || [];
		const sections = [];
		transactions.forEach((group) => {
			(group.items || []).forEach((doctype) => {
				const fieldname = (group.fieldnames && group.fieldnames[doctype]) || group.fieldname;
				if (!fieldname) return;
				sections.push(renderConnectionTable(frm, doctype, fieldname));
			});
		});

		if (!sections.length) {
			container.append(
				`<div class="text-muted small">${__("No related records yet.")}</div>`
			);
		} else {
			sections.forEach((s) => container.append(s));
		}

		dashboard.links_area && dashboard.links_area.show();
	}

	TARGET_DOCTYPES.forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			// Runs after frm.dashboard.refresh() in Frappe's own refresh_header() sequence
			// (frappe/public/js/frappe/form/form.js), so dashboard.data/transactions_area are
			// already populated by the time this fires -- no deferral needed.
			refresh(frm) {
				renderModernConnections(frm);
			},
		});
	});
})();
