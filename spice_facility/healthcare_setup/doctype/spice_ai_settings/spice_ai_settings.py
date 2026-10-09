import frappe
from frappe import _
from frappe.model.document import Document

from spice_facility.ai.config import CLAUDE, LOCAL_PROVIDERS, OPENAI


class SPICEAISettings(Document):
	def validate(self):
		if self.summary_provider not in LOCAL_PROVIDERS:
			frappe.throw(_("Summaries read patient records, so the summary provider must run locally."))
		if not self.ollama_enabled and self.summary_enabled:
			frappe.throw(_("AI summaries need the local model: enable Ollama or turn summaries off."))
		for provider, enabled, key_field, model in (
			(CLAUDE, self.claude_enabled, "anthropic_api_key", self.claude_model),
			(OPENAI, self.openai_enabled, "openai_api_key", self.openai_model),
		):
			if not enabled:
				continue
			if not self.get(key_field):
				frappe.throw(_("{0} is enabled but has no API key.").format(provider))
			if not model:
				frappe.throw(_("{0} is enabled but has no model.").format(provider))
		enabled = {"Ollama": self.ollama_enabled, CLAUDE: self.claude_enabled, OPENAI: self.openai_enabled}
		if not enabled.get(self.chat_default_provider):
			frappe.throw(_("The default chat provider {0} is not enabled.").format(self.chat_default_provider))

	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.name)
