"""Provider lookup. Callers ask for a role ("chat", "local"), never construct providers themselves."""

import frappe
from frappe import _

from spice_facility.ai.config import CLAUDE, OLLAMA, OPENAI, get_config
from spice_facility.ai.providers.anthropic import ClaudeProvider
from spice_facility.ai.providers.base import LLMProvider
from spice_facility.ai.providers.ollama import OllamaProvider
from spice_facility.ai.providers.openai import OpenAIProvider

CLASSES = {OLLAMA: OllamaProvider, CLAUDE: ClaudeProvider, OPENAI: OpenAIProvider}


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
	name = name or config.chat_default_provider
	if name not in config.chat_providers():
		frappe.throw(_("AI provider {0} is not available in chat").format(name), frappe.ValidationError)
	return build(name, config)


def local_provider() -> LLMProvider:
	"""The provider allowed to read patient data: always the local one."""
	config = get_config()
	provider = build(config.summary_provider, config)
	if not provider.is_local or not provider.config.enabled:
		frappe.throw(_("No local AI provider is enabled"), frappe.ValidationError)
	return provider
