"""Settings-form actions: test a provider and list its models. System Manager only; never returns secrets."""

import time

import frappe
from frappe import _

from spice_facility.ai.config import CLAUDE, OLLAMA, OPENAI, get_config
from spice_facility.ai.providers.base import AIProviderError
from spice_facility.ai.providers.registry import build

PROVIDERS = (OLLAMA, CLAUDE, OPENAI)


def _provider(name):
	frappe.only_for("System Manager")
	if name not in PROVIDERS:
		frappe.throw(_("Unknown AI provider {0}").format(name), frappe.ValidationError)
	return build(name, get_config())


@frappe.whitelist()
def test_provider(name: str) -> dict:
	provider = _provider(name)
	if not provider.is_local and not provider.config.api_key:
		return {"ok": False, "message": _("No API key saved for {0}.").format(name)}
	started = time.monotonic()
	try:
		models = provider.list_models()
	except AIProviderError as error:
		return {"ok": False, "message": str(error)}
	configured = provider.model
	return {
		"ok": True, "latency_ms": round((time.monotonic() - started) * 1000), "models": models[:200],
		"model_available": bool(configured and configured in models), "model": configured,
	}
