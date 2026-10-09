// Ask Data chat, shared by the full page (/desk/ask-data) and the floating drawer.
// Questions are answered by a background job (spice_facility.api.talk_to_data.ask); the answer arrives on
// the `spice_ai_answer` realtime event, with polling as a fallback.
frappe.provide("spice_facility.ai");

(() => {
	const API = "spice_facility.api.talk_to_data";
	const POLL_MS = 3000;
	const LINK_TOKEN = /\[\[([^:\]]+):([^\]]+)\]\]/g;
	const STORE_KEY = "spice_ai_chat";
	const waiting = new Map(); // log -> resolve

	frappe.realtime.on("spice_ai_answer", (data) => {
		const resolve = waiting.get(data.log);
		if (resolve && ["Answered", "Failed"].includes(data.status)) resolve();
	});

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const fmt = (value, type, options) =>
		["Float", "Currency", "Int", "Percent"].includes(type) ? frappe.format(value, { fieldtype: type === "Float" ? "Float" : type, options }) : esc(value);

	function linkify(text) {
		return esc(text).replace(LINK_TOKEN, (_m, doctype, name) =>
			`<a class="sf-ai-link" data-doctype="${esc(doctype)}" data-name="${esc(name)}">${esc(name)}</a>`);
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
					<div class="sf-ask-tools">
						<select class="form-control input-xs sf-ask-provider" title="${esc(S.provider)}">${options}</select>
						<button class="btn btn-xs btn-default sf-ask-new">${esc(S.new_chat)}</button>
					</div>
				</div>
				<div class="sf-ask-note"></div>
				<div class="sf-ask-thread"></div>
				<div class="sf-ask-examples"></div>
				<form class="sf-ask-form">
					<textarea class="form-control sf-ask-input" rows="2" placeholder="${esc(S.placeholder)}"></textarea>
					<button class="btn btn-primary btn-sm" type="submit">${esc(S.send)}</button>
				</form>`);
			this.$thread = this.$root.find(".sf-ask-thread");
			this.$root.find(".sf-ask-provider").on("change", (e) => { this.provider = e.target.value; this.render_note(); });
			this.$root.find(".sf-ask-new").on("click", () => { this.history = []; this.save_history(); this.$thread.empty(); this.render_examples(); });
			this.$root.find(".sf-ask-form").on("submit", (e) => { e.preventDefault(); this.ask(this.$root.find(".sf-ask-input").val()); });
			this.$root.find(".sf-ask-input").on("keydown", (e) => {
				if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.ask(e.target.value); }
			});
			this.$root.on("click", ".sf-ai-link", (e) => {
				e.preventDefault();
				frappe.set_route("Form", e.currentTarget.dataset.doctype, e.currentTarget.dataset.name);
			});
			this.render_note();
		}

		render_note() {
			const provider = this.settings.providers.find((p) => p.name === this.provider);
			const local = !provider || provider.is_local;
			this.$root.find(".sf-ask-note").html(
				`<span class="sf-ask-badge ${local ? "sf-local" : "sf-cloud"}">${esc(local ? this.S.local_note : this.S.cloud_note)}</span>`);
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
			this.$root.find(".sf-ask-input").val("");
			this.$root.find(".sf-ask-examples").empty();
			const $turn = this.render_question(question);
			const $answer = $(`<div class="sf-ask-answer"><span class="sf-ai-spinner"></span> ${esc(this.S.thinking)}</div>`).appendTo($turn);
			this.scroll();
			try {
				const page = use_context ? this.page() : null;
				const { log } = await frappe.xcall(`${API}.ask`, {
					question, provider: this.provider, page: page ? JSON.stringify(page) : null, use_context: use_context ? 1 : 0,
					history: JSON.stringify(this.history.slice(-2).map((t) => ({ question: t.question, spec: t.answer?.spec }))),
				});
				const result = await this.wait(log);
				const turn = { question, log, status: result.status, error: result.error, answer: result.answer };
				this.history.push(turn);
				this.save_history();
				$answer.replaceWith(this.answer_html(turn));
				this.after_render($turn, turn);
			} catch (error) {
				$answer.html(`<div class="sf-ask-error">${esc(this.S.failed)}</div>`);
			} finally {
				this.busy = false;
				this.scroll();
			}
		}

		wait(log) {
			return new Promise((resolve) => {
				let done = false;
				const finish = async () => {
					if (done) return;
					const data = await frappe.xcall(`${API}.get_answer`, { log });
					if (["Answered", "Failed"].includes(data.status)) {
						done = true;
						waiting.delete(log);
						clearInterval(timer);
						resolve(data);
					}
				};
				waiting.set(log, finish);
				const timer = setInterval(finish, POLL_MS);
			});
		}

		// ─── rendering ──────────────────────────────────────────────────────
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
			if (turn.status !== "Answered" || !a) {
				return `<div class="sf-ask-answer"><div class="sf-ask-error">${esc(S.failed)} ${esc(turn.error || "")}</div></div>`;
			}
			const number = a.number ? `<div class="sf-ask-number"><div class="sf-ask-number-value">${fmt(a.number.value, "Float")}</div><div class="sf-muted">${esc(a.number.label)}</div></div>` : "";
			const empty_result = a.mode === "data" && !a.number && !(a.table && a.table.rows.length) ? `<div class="sf-muted">${esc(S.empty)}</div>` : "";
			const table = a.table && a.table.rows.length ? `<div class="sf-ask-table-wrap"><table class="table table-sm sf-ask-table">
				<thead><tr>${a.table.columns.map((c) => `<th>${esc(c.label)}</th>`).join("")}${a.table.doctype ? "<th></th>" : ""}</tr></thead>
				<tbody>${a.table.rows.map((r) => `<tr>${a.table.columns.map((c) => `<td>${cell(r, c)}</td>`).join("")}
					${a.table.doctype && r.name ? `<td><a class="sf-ai-link" data-doctype="${esc(a.table.doctype)}" data-name="${esc(r.name)}">↗</a></td>` : ""}</tr>`).join("")}</tbody>
			</table></div>` : "";
			const local_badge = a.mode === "record" ? `<span class="sf-ask-badge sf-local">${esc(S.record_note)}</span>` : "";
			const how = a.mode === "data" ? `<details class="sf-ask-how"><summary>${esc(S.how)}</summary>
				<div>${esc(a.how || "")}</div>
				${a.scope && a.scope.length ? `<div class="sf-muted">${esc(S.scope)}: ${esc(a.scope.join(", "))}</div>` : ""}
				<div class="sf-muted">${esc(S.rows_read(a.rows_read || 0))} · ${esc(a.provider)} ${esc(a.planner || "")} → ${esc(a.narrator || "")}</div>
				<pre>${esc(JSON.stringify(a.spec, null, 1))}</pre></details>` : "";
			const actions = a.mode === "data" ? `<div class="sf-ask-actions">
				${a.list_route ? `<button class="btn btn-xs btn-default sf-act-list">${esc(S.open_list)}</button>` : ""}
				${a.chart ? `<button class="btn btn-xs btn-default sf-act-pin">${esc(S.pin)}</button>` : ""}
				${a.table ? `<button class="btn btn-xs btn-default sf-act-csv">${esc(S.csv)}</button>` : ""}
				${a.scope && a.scope.length ? `<button class="btn btn-xs btn-default sf-act-noscope">${esc(S.remove_scope)}</button>` : ""}
			</div>` : "";
			return `<div class="sf-ask-answer">
				${a.title && a.mode === "data" ? `<div class="sf-ask-title">${esc(a.title)}</div>` : ""}
				<div class="sf-ask-text">${linkify(a.text)} ${local_badge}</div>
				${number}${empty_result}
				${a.chart && a.chart.labels.length ? '<div class="sf-ask-chart"></div>' : ""}
				${table}${how}${actions}
			</div>`;
		}

		after_render($turn, turn) {
			const a = turn.answer;
			if (!a) return;
			const chart_el = $turn.find(".sf-ask-chart").get(0);
			if (chart_el && a.chart) this.draw_chart(chart_el, a);
			$turn.find(".sf-act-list").on("click", () => this.open_list(a.list_route));
			$turn.find(".sf-act-csv").on("click", () => window.open(`/api/method/${API}.export_csv?log=${encodeURIComponent(turn.log)}`));
			$turn.find(".sf-act-noscope").on("click", () => this.ask(turn.question, { use_context: false }));
			$turn.find(".sf-act-pin").on("click", () => this.pin(turn));
		}

		draw_chart(el, a) {
			const type = { bar: "bar", line: "line", pie: "pie", donut: "donut" }[a.chart.type] || "bar";
			const chart = new frappe.Chart(el, {
				type, height: 240, isNavigable: type === "bar" || type === "line" ? 1 : 0,
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
				return JSON.parse(sessionStorage.getItem(STORE_KEY) || "[]");
			} catch (e) {
				return [];
			}
		}

		save_history() {
			try {
				sessionStorage.setItem(STORE_KEY, JSON.stringify(this.history.slice(-20)));
			} catch (e) {
				// storage blocked: history lives for this view only
			}
		}
	}

	spice_facility.ai.AskData = AskData;
})();
