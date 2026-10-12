from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from spice_facility.ai.config import CLAUDE, GEMINI, KEY_FIELDS, LOCAL_PROVIDERS, OLLAMA, OPENAI, has_override

GEMINI_HOST_SUFFIX = ".googleapis.com"


class SPICEAISettings(Document):
	def validate(self):
		if self.summary_provider not in LOCAL_PROVIDERS:
			frappe.throw(_("Summaries read patient records, so the summary provider must run locally."))
		if not self.ollama_enabled and self.summary_enabled:
			frappe.throw(_("AI summaries need the local model: enable Ollama or turn summaries off."))
		for provider, enabled, model in (
			(CLAUDE, self.claude_enabled, self.claude_model),
			(OPENAI, self.openai_enabled, self.openai_model),
			(GEMINI, self.gemini_enabled, self.gemini_model),
		):
			if not enabled:
				continue
			key_field = KEY_FIELDS[provider]
			if not self.get(key_field) and not has_override(key_field):
				frappe.throw(_("{0} is enabled but has no API key.").format(provider))
			if not model:
				frappe.throw(_("{0} is enabled but has no model.").format(provider))
		if self.gemini_enabled and not _is_google_api(self.gemini_base_url):
			frappe.throw(_("The Gemini API base URL must be an https://…{0} address.").format(GEMINI_HOST_SUFFIX))
		enabled = {OLLAMA: self.ollama_enabled, CLAUDE: self.claude_enabled, OPENAI: self.openai_enabled,
		           GEMINI: self.gemini_enabled}
		if not enabled.get(self.chat_default_provider):
			frappe.throw(_("The default chat provider {0} is not enabled.").format(self.chat_default_provider))

	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.name)


def _is_google_api(url):
	"""The key, possibly a deployment secret, must only ever go to Google."""
	parsed = urlparse(url or "")
	return parsed.scheme == "https" and (parsed.hostname or "").endswith(GEMINI_HOST_SUFFIX)
