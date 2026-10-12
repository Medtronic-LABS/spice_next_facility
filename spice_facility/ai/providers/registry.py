"""Provider lookup. Callers ask for a role ("chat", "local"), never construct providers themselves."""

import frappe
from frappe import _

from spice_facility.ai.config import CLAUDE, GEMINI, OLLAMA, OPENAI, get_config
from spice_facility.ai.providers.anthropic import ClaudeProvider
from spice_facility.ai.providers.base import LLMProvider
from spice_facility.ai.providers.gemini import GeminiProvider
from spice_facility.ai.providers.ollama import OllamaProvider
from spice_facility.ai.providers.openai import OpenAIProvider

CLASSES = {OLLAMA: OllamaProvider, CLAUDE: ClaudeProvider, OPENAI: OpenAIProvider, GEMINI: GeminiProvider}


class NoLocalProvider(frappe.ValidationError):
	"""Raised when something needs the local model (patient data) but none is enabled."""


class NoSummaryProvider(frappe.ValidationError):
	"""Raised when summaries are requested but their provider is off or not allowed to read patient records."""


def build(name, config=None) -> LLMProvider:
	provider_config = (config or get_config()).provider(name)
	if not provider_config:
		frappe.throw(_("Unknown AI provider {0}").format(name), frappe.ValidationError)
	return CLASSES[name](provider_config)


def get_provider(name) -> LLMProvider:
	"""An enabled provider by name."""
	provider = build(name)
	if not provider.config.enabled:
		frappe.throw(_("AI provider {0} is not enabled").format(name), frappe.ValidationError)
	return provider


def chat_provider(name=None) -> LLMProvider:
	"""The provider a user picked for chat, or the default; only ones offered in chat."""
	config = get_config()
	available = config.chat_providers()
	if name and name not in available:
		frappe.throw(_("AI provider {0} is not available in chat").format(name), frappe.ValidationError)
	if not name:
		# The default may be switched off on this deployment (e.g. no local model): use what is available.
		name = config.chat_default_provider if config.chat_default_provider in available else next(iter(available), None)
	if not name:
		frappe.throw(_("No AI provider is enabled for chat."), frappe.ValidationError)
	return build(name, config)


def local_provider() -> LLMProvider:
	"""The provider Ask Data lets read patient data: an enabled local one, whatever writes summaries."""
	config = get_config()
	name = next((p.name for p in config.providers.values() if p.is_local and p.enabled), None)
	if not name:
		raise NoLocalProvider(_("No local AI model is configured on this server."))
	return build(name, config)


def summary_provider() -> LLMProvider:
	"""The provider that writes AI summaries: the local model, or a cloud one the site allowed to read records."""
	config = get_config()
	provider = build(config.summary_provider, config)
	if not provider.config.enabled:
		raise NoSummaryProvider(_("The AI summary provider {0} is not enabled.").format(provider.name))
	if not provider.is_local and not config.cloud_patient_data:
		raise NoSummaryProvider(_("{0} is a cloud provider and this site does not allow cloud models to read "
		                          "patient records.").format(provider.name))
	return provider


def has_local_provider() -> bool:
	return get_config().has_local()
