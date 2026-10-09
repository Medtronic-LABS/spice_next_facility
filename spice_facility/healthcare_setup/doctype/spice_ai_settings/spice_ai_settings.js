// SPICE AI Settings: test each provider and load its models. Keys are never read back to the browser;
// the server tests with the saved (encrypted) credentials, so unsaved edits are saved first.
const SPICE_AI_PROVIDERS = [
	{ name: "Ollama", enabled: "ollama_enabled", model: "ollama_model", status: "ollama_status" },
	{ name: "Claude", enabled: "claude_enabled", model: "claude_model", status: "claude_status", key: "anthropic_api_key" },
	{ name: "OpenAI", enabled: "openai_enabled", model: "openai_model", status: "openai_status", key: "openai_api_key" },
];

frappe.ui.form.on("SPICE AI Settings", {
	refresh(frm) {
		const S = spice_facility.ai.strings.settings;
		SPICE_AI_PROVIDERS.forEach((p) => {
			frm.add_custom_button(S.test(p.name), () => spice_ai_test(frm, p), S.group);
			frm.add_custom_button(S.refresh_models(p.name), () => spice_ai_test(frm, p, true), S.group);
			spice_ai_render_status(frm, p, null);
		});
	},
});

async function spice_ai_test(frm, provider, fill_models = false) {
	const S = spice_facility.ai.strings.settings;
	if (!frm.doc[provider.enabled]) {
		frappe.show_alert({ message: S.enable_first(provider.name), indicator: "orange" });
		return;
	}
	if (frm.is_dirty()) await frm.save();
	spice_ai_render_status(frm, provider, { pending: true });
	const result = await frappe.xcall("spice_facility.api.ai_settings.test_provider", { name: provider.name });
	spice_ai_render_status(frm, provider, result);
	if (fill_models && result.ok) {
		frm.set_df_property(provider.model, "options", result.models);
		frappe.show_alert({ message: S.models_loaded(result.models.length), indicator: "green" });
	}
}

function spice_ai_render_status(frm, provider, result) {
	const S = spice_facility.ai.strings.settings;
	const esc = frappe.utils.escape_html;
	const key_line = provider.key
		? `<div class="text-muted small">${esc(frm.doc[provider.key] ? S.key_saved : S.key_missing)}</div>` : "";
	let body = "";
	if (result?.pending) body = `<div class="text-muted">${esc(S.testing)}</div>`;
	else if (result && result.ok) {
		body = `<div class="indicator-pill green">${esc(S.connected(result.latency_ms))}</div>
			<div class="small mt-1">${esc(result.model_available ? S.model_ok(result.model) : S.model_missing(result.model || "—"))}</div>`;
	} else if (result) body = `<div class="indicator-pill red">${esc(S.failed)}</div><div class="small mt-1">${esc(result.message || "")}</div>`;
	frm.get_field(provider.status).$wrapper.html(`<div class="mt-2">${key_line}${body}</div>`);
}
