// AI summary block on the Overview tab (Custom HTML Block "SF AI Summary").
// Shows the cached summary, a Generate/Refresh button and live progress; the background job publishes
// `spice_ai_summary` to the document's realtime room when it starts and finishes.
frappe.provide("spice_facility.ai");

(() => {
	const METHOD = "spice_facility.api.ai_summary";
	const SEVERITY_TONE = { high: "red", medium: "orange", low: "yellow" };
	const POLL_MS = 5000; // fallback while a job is pending, in case the realtime event is missed
	const hosts = new Map(); // "doctype::name" -> render function for the block currently on screen

	frappe.realtime.on("spice_ai_summary", (data) => {
		const render = hosts.get(`${data.doctype}::${data.name}`);
		if (render) render(data.status);
	});

	function source_link(record, H) {
		if (!record || !record.includes(":")) return "";
		const [doctype, ...rest] = record.split(":");
		return H.link(doctype, rest.join(":"), "↗");
	}

	// Compare findings with alerts on their leading words: the model often restates an alert as a finding.
	const gist = (text) => (text || "").split(" — ")[0].toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "").slice(0, 24);

	// Collapsed (the default), the card shows its headline and the first alerts; the rest opens on demand.
	const COLLAPSED_ALERTS = 2;

	function render_summary(r, S, H, actions, expanded = false) {
		const s = r.summary || {};
		const alert_list = s.alerts || [];
		const alerted = new Set(alert_list.map((a) => gist(a.text)));
		const alerts = alert_list.map((a) => {
			const [what, ...where] = (a.text || "").split(" — ");
			return `<li class="sf-ai-alert sf-${SEVERITY_TONE[a.severity] || "orange"}">
				<span class="sf-ai-alert-text">${H.esc(what)}</span>
				<span class="sf-ai-alert-source">${H.esc(where.join(" — "))} ${source_link(a.record, H)}${a.origin === "ai" ? " · AI" : ""}</span>
			</li>`;
		}).join("");
		const findings = (s.key_findings || []).filter((f) => !alerted.has(gist(f.text)))
			.map((f) => `<li>${H.esc(f.text)}</li>`).join("");
		const follow = (s.follow_up || []).map((f) => `<li>${H.esc(f)}</li>`).join("");
		const hidden_alerts = Math.max(0, alert_list.length - COLLAPSED_ALERTS);
		const more = hidden_alerts || s.summary || findings || follow
			? `<button class="sf-ai-more" data-more="${H.esc(S.show_more(hidden_alerts))}" data-less="${H.esc(S.show_less)}">
				${H.esc(expanded ? S.show_less : S.show_more(hidden_alerts))}</button>` : "";
		const side = `${findings ? H.section(S.key_findings, `<ul class="sf-list">${findings}</ul>`) : ""}
			${follow ? H.section(S.follow_up, `<ul class="sf-list">${follow}</ul>`) : ""}`;
		return `
			<div class="sf-ai-card ${expanded ? "" : "sf-ai-collapsed"}">
				<div class="sf-ai-head">
					<div class="sf-ai-headline">✨ ${H.esc(s.headline || "")}</div>
					<div class="sf-ai-head-actions">
						${r.stale ? `<span class="sf-ai-pill" title="${H.esc(S.stale)}">${H.esc(S.stale_short)}</span>` : ""}
						${actions}
					</div>
				</div>
				<p class="sf-ai-text">${H.esc(s.summary || "")}</p>
				<div class="sf-ai-cols ${alerts && side.trim() ? "" : "sf-ai-single"}">
					${alerts ? H.section(S.alerts, `<ul class="sf-ai-alerts">${alerts}</ul>`) : ""}
					${side.trim() ? `<div>${side}</div>` : ""}
				</div>
				<div class="sf-ai-foot">${more}
					<span class="sf-muted sf-ai-footer">${H.esc(S.footer(r.model, frappe.datetime.prettyDate(r.generated_on)))}</span></div>
			</div>`;
	}

	spice_facility.form_ux.custom_mounts.ai_summary = async (host, frm) => {
		const S = spice_facility.ai.strings.summary;
		const H = spice_facility.form_ux.helpers;
		const key = `${frm.doctype}::${frm.doc.name}`;

		const draw = async (live_status) => {
			let data;
			try {
				data = await frappe.xcall(`${METHOD}.get`, { doctype: frm.doctype, name: frm.doc.name });
			} catch (error) {
				host.innerHTML = H.empty(S.disabled);
				return;
			}
			if (data.enabled === false) {
				// no AI access: nothing to show; summaries switched off: say so once, quietly
				host.innerHTML = data.reason === "access" ? "" : H.empty(S.disabled);
				return;
			}
			const pending = data.pending || (live_status && ["Queued", "Running"].includes(live_status));
			clearTimeout(host._sf_poll);
			if (pending && hosts.get(key) === draw && host.isConnected) host._sf_poll = setTimeout(() => draw(), POLL_MS);
			const button = `<button class="btn btn-xs btn-default sf-ai-generate" ${pending ? "disabled" : ""}>
				${H.esc(data.ready ? S.refresh : S.generate)}</button>`;
			const progress = pending
				? `<div class="sf-ai-progress"><span class="sf-ai-spinner"></span>${H.esc(data.pending?.status === "Queued" ? S.queued : S.generating)}</div>` : "";
			const failed = !pending && data.last_failed && (!data.ready || data.last_failed.generated_on > data.ready.generated_on)
				? `<div class="sf-banner sf-red">${H.esc(S.failed)} ${H.esc(data.last_failed.error || "")}</div>` : "";
			// The action sits in the card's header (or beside the empty/progress line), never on a row of its own.
			host.innerHTML = data.ready
				? `${progress}${failed}${render_summary(data.ready, S, H, button, host._sf_expanded)}`
				: `<div class="sf-ai-empty">${progress || `<span class="sf-muted">${H.esc(S.none)}</span>`}${button}</div>${failed}`;
			// open / close the full summary; kept across redraws (refresh, polling)
			host.querySelector(".sf-ai-more")?.addEventListener("click", (e) => {
				host._sf_expanded = !host._sf_expanded;
				host.querySelector(".sf-ai-card").classList.toggle("sf-ai-collapsed", !host._sf_expanded);
				e.currentTarget.textContent = host._sf_expanded ? e.currentTarget.dataset.less : e.currentTarget.dataset.more;
			});
			host.querySelector(".sf-ai-generate")?.addEventListener("click", async () => {
				try {
					await frappe.xcall(`${METHOD}.generate`, { doctype: frm.doctype, name: frm.doc.name });
					draw("Queued");
				} catch (error) {
					draw();
				}
			});
		};

		hosts.set(key, draw);
		await draw();
	};
})();
