// Ask Data chat, shared by the full page (/desk/ask-data) and the floating drawer.
// Questions are answered by a background job (spice_facility.api.talk_to_data.ask); the answer arrives on
// the `spice_ai_answer` realtime event, with polling as a fallback.
frappe.provide("spice_facility.ai");

(() => {
	const API = "spice_facility.api.talk_to_data";
	const POLL_MS = 3000;
	const LINK_TOKEN = /\[\[([^:\]]+):([^\]]+)\]\]/g;
	// Chat history is per user: a shared desk tab must not show the previous user's questions or patients.
	const STORE_PREFIX = "spice_ai_chat::";
	const store_key = () => STORE_PREFIX + frappe.session.user;
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
	// Groups beyond this, or with long names, read better as a ranked list than as squeezed bars.
	const MAX_CHART_BARS = 8;
	const MAX_CHART_LABEL = 12;
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

	// What to draw for a result: a Frappe chart, a ranked bar list, or nothing (the KPI line says it all).
	function visual_kind(a) {
		const labels = a.chart?.labels || [];
		if (!labels.length) return null;
		if (a.kind === "series") return labels.length > 1 ? "chart" : null;
		if (["pie", "donut"].includes(a.chart.type)) return "chart";
		const long = labels.some((l) => String(l).length > MAX_CHART_LABEL);
		return labels.length > MAX_CHART_BARS || long ? "bars" : "chart";
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

	class AskData {
		constructor(parent, { mode = "page", get_page = () => null } = {}) {
			this.S = spice_facility.ai.strings.chat;
			this.mode = mode;
			this.get_page = get_page;
			this.use_context = true;
			this.history = this.load_history();
			this.$root = $(`<div class="sf-ask sf-ask-${mode}"></div>`).appendTo(parent);
			this.init();
		}

		async init() {
			this.settings = await frappe.xcall(`${API}.settings`);
			if (!this.settings.chat_enabled) {
				this.$root.html(`<div class="sf-ask-empty">${esc(spice_facility.ai.strings.summary.disabled)}</div>`);
				return;
			}
			this.provider = this.settings.default_provider;
			this.render_shell();
			this.history.forEach((turn) => this.render_turn(turn));
			this.refresh_context();
		}

		// ─── shell ──────────────────────────────────────────────────────────
		render_shell() {
			const S = this.S;
			const options = this.settings.providers.map((p) =>
				`<option value="${esc(p.name)}" ${p.name === this.provider ? "selected" : ""}>${esc(p.name)} · ${esc(p.model || "")}</option>`).join("");
			this.$root.html(`
				<div class="sf-ask-head">
					<div class="sf-ask-context"></div>
					<button class="btn btn-xs btn-default sf-ask-new">${esc(S.new_chat)}</button>
				</div>
				<div class="sf-ask-thread"></div>
				<div class="sf-ask-examples"></div>
				<form class="sf-ask-form">
					<div class="sf-ask-disclaimer">${esc(S.disclaimer)}</div>
					<div class="sf-ask-composer">
						<textarea class="sf-ask-input" rows="1" placeholder="${esc(S.placeholder)}" aria-label="${esc(S.placeholder)}"
							data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false"></textarea>
						<div class="sf-ask-composer-tools">
							<span class="sf-ask-where"></span>
							<select class="sf-ask-provider" title="${esc(S.provider)}" aria-label="${esc(S.provider)}">${options}</select>
							<button class="sf-ask-send" type="submit" title="${esc(S.send)}" aria-label="${esc(S.send)}">${icon("arrow-up")}</button>
						</div>
					</div>
				</form>`);
			this.$thread = this.$root.find(".sf-ask-thread");
			this.$root.find(".sf-ask-provider").on("change", (e) => { this.provider = e.target.value; this.render_note(); });
			this.$root.find(".sf-ask-new").on("click", () => { this.history = []; this.save_history(); this.$thread.empty(); this.render_examples(); });
			// While a question is being answered the send button is a Stop button.
			this.$root.find(".sf-ask-form").on("submit", (e) => {
				e.preventDefault();
				if (this.busy) this.stop();
				else this.ask(this.$root.find(".sf-ask-input").val());
			});
			this.$root.find(".sf-ask-input").on("keydown", (e) => {
				if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.ask(e.target.value); }
			}).on("input", (e) => this.fit_input(e.target));
			$(document).on("click", (e) => {
				if (!$(e.target).closest(".sf-ask-sources, .sf-ask-src").length) this.close_sources();
			});
			$(document).on("keydown", (e) => {
				if (e.key === "Escape") this.close_sources();
			});
			this.$root.on("click", ".sf-ai-link", (e) => {
				e.preventDefault();
				this.close_sources();
				frappe.set_route("Form", e.currentTarget.dataset.doctype, e.currentTarget.dataset.name);
			});
			this.render_note();
		}

		render_note() {
			const provider = this.settings.providers.find((p) => p.name === this.provider);
			const local = !provider || provider.is_local;
			const note = local ? this.S.local_note : this.S.cloud_note;
			this.$root.find(".sf-ask-where").attr({ title: note, "aria-label": note })
				.toggleClass("sf-cloud", !local).toggleClass("sf-local", local).html(icon(local ? "server" : "cloud"));
		}

		refresh_context() {
			const page = this.page();
			const $ctx = this.$root.find(".sf-ask-context");
			if (!page) $ctx.empty();
			else {
				$ctx.html(`<button class="sf-ask-chip ${this.use_context ? "active" : ""}" title="${esc(this.use_context ? this.S.context : this.S.context_off)}">
					${esc(page.doctype)}${page.name ? " · " + esc(page.label || page.name) : ""}</button>`);
				$ctx.find(".sf-ask-chip").on("click", () => { this.use_context = !this.use_context; this.refresh_context(); });
			}
			this.render_examples();
		}

		render_examples() {
			const page = this.use_context ? this.page() : null;
			const examples = (page && page.name && this.S.examples[page.doctype]) || this.S.examples.default;
			const $ex = this.$root.find(".sf-ask-examples");
			$ex.html(this.$thread.children().length ? "" : examples.map((q) => `<button class="sf-ask-example">${esc(q)}</button>`).join(""));
			$ex.find(".sf-ask-example").on("click", (e) => this.ask(e.currentTarget.textContent));
		}

		page() {
			return this.get_page ? this.get_page() : null;
		}

		// ─── asking ─────────────────────────────────────────────────────────
		async ask(question, { use_context = this.use_context } = {}) {
			question = (question || "").trim();
			if (!question || this.busy) return;
			this.busy = true;
			const pending = (this.pending = { stopped: false, log: null, end: null });
			this.set_sending(true);
			this.fit_input(this.$root.find(".sf-ask-input").val("").get(0));
			this.$root.find(".sf-ask-examples").empty();
			const $turn = this.render_question(question);
			const $answer = $(this.pending_html()).appendTo($turn);
			const started = Date.now();
			const tick = setInterval(() => $answer.find(".sf-ask-elapsed").text(this.S.elapsed(Math.round((Date.now() - started) / 1000))), 1000);
			this.scroll();
			try {
				const page = use_context ? this.page() : null;
				const { log } = await frappe.xcall(`${API}.ask`, {
					question, provider: this.provider, page: page ? JSON.stringify(page) : null, use_context: use_context ? 1 : 0,
					history: JSON.stringify(this.history.slice(-2).map((t) => ({ question: t.question, spec: t.answer?.spec }))),
				});
				pending.log = log;
				const result = pending.stopped
					? (this.request_stop(log), { status: "Stopped" })
					: await this.wait(log, (status) =>
						$answer.find(".sf-ask-pending-text").text(status === "Queued" ? this.S.queued : this.S.thinking), pending);
				const turn = { question, log, status: result.status, error: result.error, answer: result.answer,
				               asked_on: result.asked_on, duration_ms: result.duration_ms };
				this.history.push(turn);
				this.save_history();
				$answer.replaceWith(this.answer_html(turn));
				this.after_render($turn, turn);
			} catch (error) {
				$answer.removeClass("sf-ask-pending").html(`<div class="sf-ask-error">${esc(this.S.failed)}</div>`);
			} finally {
				clearInterval(tick);
				this.busy = false;
				this.pending = null;
				this.set_sending(false);
				this.scroll();
			}
		}

		wait(log, on_status = () => {}, pending = null) {
			return new Promise((resolve) => {
				let done = false;
				const end = (data) => {
					done = true;
					waiting.delete(log);
					clearInterval(timer);
					resolve(data);
				};
				const finish = async () => {
					if (done) return;
					const data = await frappe.xcall(`${API}.get_answer`, { log });
					if (done) return;
					on_status(data.status);
					if (["Answered", "Failed", "Stopped"].includes(data.status)) end(data);
				};
				if (pending) pending.end = () => !done && end({ status: "Stopped" });
				waiting.set(log, finish);
				const timer = setInterval(finish, POLL_MS);
			});
		}

		// Stop the question in flight: the turn ends now; the server drops the queued job or discards its answer.
		stop() {
			const pending = this.pending;
			if (!pending || pending.stopped) return;
			pending.stopped = true;
			pending.end?.();
			if (pending.log) this.request_stop(pending.log);
		}

		request_stop(log) {
			frappe.xcall(`${API}.stop`, { log }).catch(() => {
				// the turn is already shown as stopped; a late answer is simply never displayed
			});
		}

		set_sending(sending) {
			const S = this.S;
			this.$root.find(".sf-ask-send").toggleClass("sf-stop", sending)
				.attr({ title: sending ? S.stop : S.send, "aria-label": sending ? S.stop : S.send })
				.html(icon(sending ? "square" : "arrow-up"));
		}

		// ─── rendering ──────────────────────────────────────────────────────
		// While the answer is being worked out: what is happening, how long so far, and the shape of the card to come.
		pending_html() {
			return `<div class="sf-ask-answer sf-ask-pending" role="status" aria-live="polite">
				<div class="sf-ask-pending-head">
					<span class="sf-ask-spark sf-ask-pulse">${icon("sparkles")}</span>
					<span class="sf-ask-pending-text">${esc(this.S.thinking)}</span>
					<span class="sf-ask-elapsed"></span>
				</div>
				<div class="sf-ask-skeleton" aria-hidden="true"><span></span><span></span><span></span></div>
			</div>`;
		}

		render_question(question) {
			return $(`<div class="sf-ask-turn"><div class="sf-ask-q">${esc(question)}</div></div>`).appendTo(this.$thread);
		}

		render_turn(turn) {
			const $turn = this.render_question(turn.question);
			$turn.append(this.answer_html(turn));
			this.after_render($turn, turn);
		}

		answer_html(turn) {
			const S = this.S;
			const a = turn.answer;
			if (turn.status === "Stopped") {
				return `<div class="sf-ask-answer sf-ask-stopped"><span class="sf-muted">${esc(S.stopped)}</span>
					<button class="btn btn-xs btn-default sf-ask-resend">${icon("rotate-ccw")} ${esc(S.resend)}</button></div>`;
			}
			if (turn.status !== "Answered" || !a) {
				return `<div class="sf-ask-answer"><div class="sf-ask-error">${esc(S.failed)} ${esc(turn.error || "")}</div></div>`;
			}
			const labels = Object.fromEntries((a.sources || []).map((r) => [`${r.doctype}:${r.name}`, r.label]));
			const visual = visual_kind(a);
			const figure = kpi(a);
			const rows = a.table?.rows || [];
			const empty_result = a.mode === "data" && !a.number && !rows.length ? `<div class="sf-muted">${esc(S.empty)}</div>` : "";
			return `<div class="sf-ask-answer">
				${this.card_head(a)}
				${this.sources_panel(turn)}
				${figure ? `<div class="sf-ask-kpi"><span class="sf-ask-kpi-value">${fmt(figure.value, "Float")}</span>
					<span class="sf-ask-kpi-label">${esc(figure.label)}</span></div>` : ""}
				<div class="sf-ask-text">${linkify(a.text, labels)}</div>
				${empty_result}
				${visual === "chart" ? '<div class="sf-ask-chart"></div>' : ""}
				${visual === "bars" ? this.bar_list(a) : ""}
				${rows.length ? this.table_html(a, !visual && !figure) : ""}
			</div>`;
		}

		card_head(a) {
			const S = this.S;
			const actions = a.mode === "data" ? [
				a.list_route && `<a class="dropdown-item sf-act-list">${icon("list")} ${esc(S.open_list)}</a>`,
				a.chart && `<a class="dropdown-item sf-act-pin">${icon("pin")} ${esc(S.pin)}</a>`,
				a.table && `<a class="dropdown-item sf-act-csv">${icon("download")} ${esc(S.csv)}</a>`,
				a.scope?.length && `<a class="dropdown-item sf-act-noscope">${icon("rotate-ccw")} ${esc(S.remove_scope)}</a>`,
			].filter(Boolean) : [];
			const menu = actions.length ? `<div class="dropdown">
				<button class="btn btn-xs btn-default sf-ask-icon-btn" data-toggle="dropdown" title="${esc(S.more_actions)}" aria-label="${esc(S.more_actions)}">${icon("ellipsis")}</button>
				<div class="dropdown-menu dropdown-menu-right">${actions.join("")}</div></div>` : "";
			const title = a.mode === "data" && a.title;
			return `<div class="sf-ask-card-head ${title ? "" : "sf-bare"}">
				${title ? `<div class="sf-ask-title"><span class="sf-ask-spark">${icon("sparkles")}</span>${esc(a.title)}</div>` : ""}
				<button class="btn btn-xs btn-default sf-ask-icon-btn sf-ask-src" title="${esc(S.sources_hint)}" aria-label="${esc(S.sources)}">${icon("info")}</button>
				${menu}
			</div>`;
		}

		sources_panel(turn) {
			const S = this.S;
			const a = turn.answer;
			const section = (title, body, name) => (body ? `<div class="sf-ask-src-section">
				<span class="sf-ask-src-icon">${icon(name)}</span>
				<div><div class="sf-ask-src-title">${esc(title)}</div>${body}</div></div>` : "");
			const line = (text) => (text ? `<div>${esc(text)}</div>` : "");
			let where = "";
			let used = "";
			if (a.mode === "record") {
				used = (a.sources || []).map((r) => `<div>${record_chip(r.doctype, r.name, r.label)}</div>`).join("");
			} else {
				const src = a.source;
				const dataset = src ? [src.dataset, src.measure, src.group_by, src.bucket].filter(Boolean).join(" · ") : a.how;
				const list = a.list_route ? `<a class="sf-act-list sf-ask-src-link">${icon("external-link")} ${esc(S.open_list)}</a>` : "";
				where = section(S.data_from, `<div>${esc(dataset || "")}</div>${list}`, "database", 1)
					+ section(S.filters, (src?.filters || []).map(line).join(""), "funnel")
					+ section(S.period, line(src?.period_from && `${frappe.datetime.str_to_user(src.period_from)} – ${frappe.datetime.str_to_user(src.period_to)}`), "calendar", 5)
					+ section(S.from_page, (src?.scope || a.scope || []).map(line).join(""), "map-pin")
					+ section(S.records, line(S.rows_read(a.rows_read || 0) + (a.truncated && a.table ? ` · ${S.truncated(a.table.rows.length)}` : "")), "hash", 2);
			}
			const how = a.mode === "record"
				? line(S.record_answer(a.narrator || a.planner || ""))
				: line(S.planned_by(a.provider || "", a.planner || "")) + line(a.planner_is_local ? S.planned_local : S.planned_cloud)
					+ line(a.narrator ? S.written_by(a.narrator) : S.summarised);
			const asked = turn.asked_on
				? line([frappe.datetime.prettyDate(turn.asked_on), turn.duration_ms && S.took((turn.duration_ms / 1000).toFixed(1))].filter(Boolean).join(" · "))
				: "";
			const technical = a.spec && frappe.user.has_role("System Manager")
				? `<details class="sf-ask-src-tech"><summary>${esc(S.technical)}</summary><pre>${esc(JSON.stringify(a.spec, null, 1))}</pre></details>` : "";
			return `<div class="sf-ask-sources" hidden>
				<div class="sf-ask-src-head">${icon("info")} ${esc(S.sources)}</div>
				${where}${section(S.records_used, used, "file-text")}${section(S.answered, how, "sparkles")}${section(S.asked, asked, "clock")}${technical}
			</div>`;
		}

		bar_list(a) {
			const values = a.chart.datasets[0].values;
			const max = Math.max(...values.map((v) => Number(v) || 0), 1);
			return `<div class="sf-ask-bars">${a.chart.labels.map((label, i) => `
				<button class="sf-ask-bar" data-index="${i}" ${a.drill?.[i] ? "" : "disabled"}>
					<span class="sf-ask-bar-label">${esc(label)}</span>
					<span class="sf-ask-bar-track"><span class="sf-ask-bar-fill" style="width:${((Number(values[i]) || 0) / max) * 100}%"></span></span>
					<span class="sf-ask-bar-value">${fmt(values[i], "Float")}</span>
				</button>`).join("")}</div>`;
		}

		table_html(a, open) {
			const rows = a.table.rows;
			const table = `<div class="sf-ask-table-wrap"><table class="table table-sm sf-ask-table">
				<thead><tr>${a.table.columns.map((c) => `<th>${esc(c.label)}</th>`).join("")}${a.table.doctype ? "<th></th>" : ""}</tr></thead>
				<tbody>${rows.map((r) => `<tr>${a.table.columns.map((c) => `<td>${cell(r, c)}</td>`).join("")}
					${a.table.doctype && r.name ? `<td><a class="sf-ai-link" data-doctype="${esc(a.table.doctype)}" data-name="${esc(r.name)}">${icon("external-link")}</a></td>` : ""}</tr>`).join("")}</tbody>
			</table></div>`;
			if (open) return table;
			return `<button class="btn btn-link btn-xs sf-ask-table-toggle" data-show="${esc(this.S.show_table(rows.length))}" data-hide="${esc(this.S.hide_table)}">${esc(this.S.show_table(rows.length))}</button>
				<div class="sf-ask-table-box" hidden>${table}</div>`;
		}

		after_render($turn, turn) {
			$turn.find(".sf-ask-resend").on("click", () => this.ask(turn.question));
			const a = turn.answer;
			if (!a) return;
			const chart_el = $turn.find(".sf-ask-chart").get(0);
			if (chart_el && a.chart) this.draw_chart(chart_el, a);
			$turn.find(".sf-act-list").on("click", () => this.open_list(a.list_route));
			$turn.find(".sf-act-csv").on("click", () => window.open(`/api/method/${API}.export_csv?log=${encodeURIComponent(turn.log)}`));
			$turn.find(".sf-act-noscope").on("click", () => this.ask(turn.question, { use_context: false }));
			$turn.find(".sf-act-pin").on("click", () => this.pin(turn));
			$turn.find(".sf-ask-src").on("click", (e) => {
				const open = $turn.find(".sf-ask-sources").prop("hidden");
				this.close_sources();
				$turn.find(".sf-ask-sources").prop("hidden", !open);
				e.currentTarget.setAttribute("aria-expanded", String(open));
			});
			$turn.find(".sf-ask-table-toggle").on("click", (e) => {
				const $box = $turn.find(".sf-ask-table-box");
				const show = $box.prop("hidden");
				$box.prop("hidden", !show);
				e.currentTarget.textContent = show ? e.currentTarget.dataset.hide : e.currentTarget.dataset.show;
			});
			$turn.find(".sf-ask-bar").on("click", (e) => {
				const options = a.drill?.[e.currentTarget.dataset.index];
				if (options) this.open_list({ doctype: a.list_route.doctype, options });
			});
		}

		// The box grows with each new line (Shift+Enter or wrapping) up to a cap, then scrolls.
		fit_input(el) {
			if (!el) return;
			el.style.height = "auto";
			el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`;
			el.closest(".sf-ask-composer")?.classList.toggle("sf-multiline", el.scrollHeight > SINGLE_LINE_HEIGHT);
		}

		close_sources() {
			this.$root.find(".sf-ask-sources").prop("hidden", true);
			this.$root.find(".sf-ask-src").attr("aria-expanded", "false");
		}

		draw_chart(el, a) {
			const type = { bar: "bar", line: "line", pie: "pie", donut: "donut" }[a.chart.type] || "bar";
			const chart = new frappe.Chart(el, {
				type, height: 220, isNavigable: type === "bar" || type === "line" ? 1 : 0,
				colors: palette(),
				data: { labels: a.chart.labels, datasets: a.chart.datasets.map((d) => ({ name: d.name, values: d.values })) },
				axisOptions: { xIsSeries: type === "line" ? 1 : 0 },
				lineOptions: { regionFill: 1, dotSize: 4 },
			});
			if (a.drill) {
				el.addEventListener("data-select", (e) => {
					const opts = a.drill[e.index];
					if (opts) this.open_list({ doctype: a.list_route.doctype, options: opts });
				});
			}
			return chart;
		}

		open_list(route) {
			frappe.route_options = route.options;
			frappe.set_route("List", route.doctype);
		}

		pin(turn) {
			frappe.prompt({ fieldname: "title", fieldtype: "Data", label: this.S.pin_title, reqd: 1, default: turn.answer.title },
				async (values) => {
					const r = await frappe.xcall(`${API}.pin_chart`, { log: turn.log, title: values.title });
					frappe.show_alert({ message: this.S.pinned(r.name || values.title), indicator: "green" });
				}, this.S.pin);
		}

		scroll() {
			const el = this.mode === "drawer" ? this.$thread.get(0) : this.$root.get(0);
			if (el) el.scrollTop = el.scrollHeight;
			this.$thread.children().last().get(0)?.scrollIntoView({ block: "end", behavior: "smooth" });
		}

		// ─── per-tab history ────────────────────────────────────────────────
		load_history() {
			try {
				Object.keys(sessionStorage).filter((k) => (k.startsWith(STORE_PREFIX) && k !== store_key()) || k === "spice_ai_chat")
					.forEach((k) => sessionStorage.removeItem(k));
				return JSON.parse(sessionStorage.getItem(store_key()) || "[]");
			} catch (e) {
				return [];
			}
		}

		save_history() {
			try {
				sessionStorage.setItem(store_key(), JSON.stringify(this.history.slice(-20)));
			} catch (e) {
				// storage blocked: history lives for this view only
			}
		}
	}

	spice_facility.ai.AskData = AskData;
})();
