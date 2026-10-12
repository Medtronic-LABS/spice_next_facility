// Ask Data: constants and pure helpers shared by the chat's parts (answer_card, chat_list, composer, ask_data_view).
frappe.provide("spice_facility.ai.ask");

(() => {
	const API = "spice_facility.api.talk_to_data";
	const POLL_MS = 3000;
	const LINK_TOKEN = /\[\[([^:\]]+):([^\]]+)\]\]/g;
	// Conversations live on the server (talk_to_data.threads / thread); the tab remembers only which one is
	// open, per user. Older builds kept whole answers here: those keys are removed on load.
	const STORE_PREFIX = "spice_ai_thread::";
	const LEGACY_PREFIX = "spice_ai_chat";
	const store_key = () => STORE_PREFIX + frappe.session.user;
	// Whether the chat list is shown on the full page: a per-viewer preference, kept across visits.
	const SIDE_PREF = "spice_ai_chat_list_hidden";
	// Below this width the page behaves like the drawer: the chat list covers the chat instead of sitting beside it.
	const NARROW = "(max-width: 860px)";
	const waiting = new Map(); // log -> resolve

	frappe.realtime.on("spice_ai_answer", (data) => {
		const resolve = waiting.get(data.log);
		if (resolve && ["Answered", "Failed", "Stopped"].includes(data.status)) resolve();
	});

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const fmt = (value, type, options) =>
		["Float", "Currency", "Int", "Percent"].includes(type) ? frappe.format(value, { fieldtype: type === "Float" ? "Float" : type, options }) : esc(value);

	// Icons for the record chips in answers; other doctypes get a plain file icon.
	const DOCTYPE_ICONS = {
		"Patient": "user", "Lab Test": "flask-conical", "Patient Encounter": "stethoscope", "Vital Signs": "heart-pulse",
		"Patient Appointment": "calendar", "Sales Invoice": "receipt", "Inpatient Record": "bed-double",
	};
	// Chart colours must be real values (Frappe Charts draws SVG), so they are read from the palette in ai.css:
	// the accent first (single-series bar/line), then muted tones for pie slices.
	const PALETTE_SIZE = 6;
	const palette = () => Array.from({ length: PALETTE_SIZE }, (_v, i) =>
		getComputedStyle(document.documentElement).getPropertyValue(`--sf-c${i + 1}`).trim()).filter(Boolean);
	const icon = (name) => frappe.utils.icon(name, "sm");
	const MAX_INPUT_HEIGHT = 200; // px, about 8 lines
	const SINGLE_LINE_HEIGHT = 40; // px: taller than this means the question wrapped

	function record_chip(doctype, name, label) {
		return `<a class="sf-ai-link sf-ask-chip-link" data-doctype="${esc(doctype)}" data-name="${esc(name)}" title="${esc(__(doctype))} ${esc(name)}">
			${icon(DOCTYPE_ICONS[doctype] || "file-text")}<span>${esc(label || name)}</span></a>`;
	}

	// Split on the raw text, then escape each piece: matching after escaping would mangle names with & or '.
	function linkify(text, labels = {}) {
		const raw = text == null ? "" : String(text);
		let html = "";
		let last = 0;
		for (const m of raw.matchAll(LINK_TOKEN)) {
			html += esc(raw.slice(last, m.index)) + record_chip(m[1], m[2], labels[`${m[1]}:${m[2]}`]);
			last = m.index + m[0].length;
		}
		return html + esc(raw.slice(last));
	}

	// What to draw for a result: groups as a ranked, clickable bar list (full names, drill-down per row); trends and
	// pies as a Frappe chart; nothing for a single period (the KPI line says it all).
	function visual_kind(a) {
		const labels = a.chart?.labels || [];
		if (!labels.length) return null;
		if (a.kind === "series") return labels.length > 1 ? "chart" : null;
		return ["pie", "donut"].includes(a.chart.type) ? "chart" : "bars";
	}

	// The headline figure: a number answer, or the server's total across all groups or periods.
	function kpi(a) {
		if (a.number) return { value: a.number.value, label: a.number.label };
		// `total` is computed on the server over every record read; the chart may hold only the top groups.
		if (a.total == null) return null;
		const agg = a.source?.aggregate || a.spec?.measure?.agg;
		const series_name = a.chart?.datasets?.[0]?.name || "";
		const label = agg === "count" ? (a.source?.dataset || series_name) : series_name;
		const single_period = a.kind === "series" && a.chart?.labels?.length === 1;
		return { value: a.total, label: single_period ? `${label} · ${a.chart.labels[0]}` : label };
	}

	function cell(row, col, table_doctype) {
		const value = col.field === "label" ? row.label : row[col.field];
		const shown = col.field === "label" && row.display != null ? row.display : value;
		if (value == null || value === "") return "—";
		if (col.type === "Link" && col.options) return `<a class="sf-ai-link" data-doctype="${esc(col.options)}" data-name="${esc(value)}">${esc(shown)}</a>`;
		if (col.type === "Date") return esc(frappe.datetime.str_to_user(value));
		if (col.type === "Datetime") return esc(frappe.datetime.str_to_user(value));
		return fmt(shown, col.type, col.options);
	}

	spice_facility.ai.ask.lib = {
		API, POLL_MS, STORE_PREFIX, LEGACY_PREFIX, store_key, SIDE_PREF, NARROW, waiting, esc, fmt, icon, palette, MAX_INPUT_HEIGHT, SINGLE_LINE_HEIGHT, record_chip, linkify, visual_kind, kpi, cell,
	};
})();
