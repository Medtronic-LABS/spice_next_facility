// Ask Data: rendering one answer — pending state, the answer card (KPI, visual, table, Sources panel, model tag)
// and its actions. Mixed into AskData (ask_data_view.js); methods use the view's state.
frappe.provide("spice_facility.ai.ask");

(() => {
	const { API, esc, fmt, icon, palette, record_chip, linkify, visual_kind, kpi, cell } = spice_facility.ai.ask.lib;

	spice_facility.ai.ask.answer_card = {
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
		},

		answer_html(turn) {
			const S = this.S;
			const a = turn.answer;
			if (["Queued", "Running"].includes(turn.status)) {
				return `<div class="sf-ask-answer"><span class="sf-muted">${esc(S.still_working)}</span></div>`;
			}
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
				${this.card_head(a, turn)}
				${this.sources_panel(turn)}
				${figure ? `<div class="sf-ask-kpi"><span class="sf-ask-kpi-value">${fmt(figure.value, "Float")}</span>
					<span class="sf-ask-kpi-label">${esc(figure.label)}</span></div>` : ""}
				${a.record_fallback ? `<div class="sf-ask-fallback">${icon("info")}<span>${esc(S.record_fallback)}</span></div>` : ""}
				<div class="sf-ask-text">${linkify(a.text, labels)}</div>
				${empty_result}
				${visual === "chart" ? '<div class="sf-ask-chart"></div>' : ""}
				${visual === "bars" ? this.bar_list(a) : ""}
				${rows.length ? this.table_html(a, !visual && !figure) : ""}
			</div>`;
		},

		// The model the user picked for this question (as logged), shown on every answer, including reopened chats.
		model_tag(turn) {
			const a = turn.answer || {};
			const provider = turn.provider || a.provider;
			const model = turn.model || a.planner;
			if (!model) return "";
			const local = a.planner_is_local ?? this.settings.providers.find((p) => p.name === provider)?.is_local;
			const label = this.S.answered_with(provider || "", model);
			return `<span class="sf-ask-model ${local ? "sf-local" : "sf-cloud"}" title="${esc(label)}" aria-label="${esc(label)}">
				${icon(local ? "server" : "cloud")}<span>${esc(model)}</span></span>`;
		},

		card_head(a, turn) {
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
				${this.model_tag(turn)}
				<button class="btn btn-xs btn-default sf-ask-icon-btn sf-ask-src" title="${esc(S.sources_hint)}" aria-label="${esc(S.sources)}">${icon("info")}</button>
				${menu}
			</div>`;
		},

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
				? line(a.narrator_is_local === false ? S.record_answer_cloud(a.narrator || "") : S.record_answer(a.narrator || a.planner || ""))
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
		},

		bar_list(a) {
			const values = a.chart.datasets[0].values;
			const max = Math.max(...values.map((v) => Number(v) || 0), 1);
			return `<div class="sf-ask-bars">${a.chart.labels.map((label, i) => `
				<button class="sf-ask-bar" data-index="${i}" ${a.drill?.[i] ? "" : "disabled"}>
					<span class="sf-ask-bar-label">${esc(label)}</span>
					<span class="sf-ask-bar-track"><span class="sf-ask-bar-fill" style="width:${((Number(values[i]) || 0) / max) * 100}%"></span></span>
					<span class="sf-ask-bar-value">${fmt(values[i], "Float")}</span>
				</button>`).join("")}</div>`;
		},

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
		},

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
		},

		close_sources() {
			this.$root.find(".sf-ask-sources").prop("hidden", true);
			this.$root.find(".sf-ask-src").attr("aria-expanded", "false");
		},

		draw_chart(el, a) {
			const type = { bar: "bar", line: "line", pie: "pie", donut: "donut" }[a.chart.type] || "bar";
			return new frappe.Chart(el, {
				// no isNavigable: Frappe Charts throws on every window resize with it; drill-down lives in the bar list
				type, height: 220,
				colors: palette(),
				data: { labels: a.chart.labels, datasets: a.chart.datasets.map((d) => ({ name: d.name, values: d.values })) },
				axisOptions: { xIsSeries: type === "line" ? 1 : 0 },
				lineOptions: { regionFill: 1, dotSize: 4 },
			});
		},

		open_list(route) {
			frappe.route_options = route.options;
			frappe.set_route("List", route.doctype);
		},

		pin(turn) {
			frappe.prompt({ fieldname: "title", fieldtype: "Data", label: this.S.pin_title, reqd: 1, default: turn.answer.title },
				async (values) => {
					const r = await frappe.xcall(`${API}.pin_chart`, { log: turn.log, title: values.title });
					frappe.show_alert({ message: this.S.pinned(r.name || values.title), indicator: "green" });
				}, this.S.pin);
		},
	};
})();
