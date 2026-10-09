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

	function render_summary(r, S, H) {
		const s = r.summary || {};
		const alerts = (s.alerts || []).map((a) => `<div class="sf-ai-alert sf-${SEVERITY_TONE[a.severity] || "orange"}">
			${H.esc(a.text)} ${source_link(a.record, H)}${a.origin === "ai" ? ' <span class="sf-muted">· AI</span>' : ""}</div>`).join("");
		const findings = (s.key_findings || []).map((f) => `<li>${H.esc(f.text)}</li>`).join("");
		const follow = (s.follow_up || []).map((f) => `<li>${H.esc(f)}</li>`).join("");
		return `
			<div class="sf-ai-card">
				<div class="sf-ai-headline">✨ ${H.esc(s.headline || "")}</div>
				${r.stale ? `<div class="sf-banner sf-orange">${H.esc(S.stale)}</div>` : ""}
				<p class="sf-ai-text">${H.esc(s.summary || "")}</p>
				${alerts ? H.section(S.alerts, alerts) : ""}
				<div class="sf-grid-3">
					${findings ? H.section(S.key_findings, `<ul class="sf-list">${findings}</ul>`) : ""}
					${follow ? H.section(S.follow_up, `<ul class="sf-list">${follow}</ul>`) : ""}
				</div>
				<div class="sf-muted sf-ai-footer">${H.esc(S.footer(r.model, frappe.datetime.prettyDate(r.generated_on)))}</div>
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
			const pending = data.pending || (live_status && ["Queued", "Running"].includes(live_status));
			clearTimeout(host._sf_poll);
			if (pending && hosts.get(key) === draw && host.isConnected) host._sf_poll = setTimeout(() => draw(), POLL_MS);
			const button = `<button class="btn btn-xs btn-default sf-ai-generate" ${pending ? "disabled" : ""}>
				${H.esc(data.ready ? S.refresh : S.generate)}</button>`;
			const progress = pending
				? `<div class="sf-ai-progress"><span class="sf-ai-spinner"></span>${H.esc(data.pending?.status === "Queued" ? S.queued : S.generating)}</div>` : "";
			const failed = !pending && data.last_failed && (!data.ready || data.last_failed.generated_on > data.ready.generated_on)
				? `<div class="sf-banner sf-red">${H.esc(S.failed)} ${H.esc(data.last_failed.error || "")}</div>` : "";
			const body = data.ready ? render_summary(data.ready, S, H) : (pending ? "" : H.empty(S.none));
			// The section above already says "AI summary"; the block only adds its action.
			host.innerHTML = `<div class="sf-ai-actions">${button}</div>
				${progress}${failed}${body}`;
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
