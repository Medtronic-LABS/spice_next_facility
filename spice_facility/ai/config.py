"""The single typed accessor for AI configuration (SPICE AI Settings + site_config overrides).

Nothing else reads the settings doctype or secrets directly. A site_config / environment value wins over
the stored setting, so CI and deployments can inject credentials without touching the database.
"""

import os
from dataclasses import dataclass, field

import frappe

SETTINGS = "SPICE AI Settings"
OLLAMA, CLAUDE, OPENAI = "Ollama", "Claude", "OpenAI"
LOCAL_PROVIDERS = (OLLAMA,)

# site_config / env keys that override stored values
OVERRIDES = {
	"ollama_enabled": "spice_ai_ollama_enabled",
	"ollama_url": "spice_ai_ollama_url",
	"anthropic_api_key": "spice_ai_anthropic_api_key",
	"openai_api_key": "spice_ai_openai_api_key",
}


@dataclass(frozen=True)
class ProviderConfig:
	name: str
	enabled: bool
	model: str | None
	base_url: str | None
	timeout: int
	in_chat: bool
	is_local: bool
	api_key: str | None = field(default=None, repr=False)
	keep_alive: str | None = None
	temperature: float = 0.1


@dataclass(frozen=True)
class AIConfig:
	providers: dict
	summary_enabled: bool
	chat_enabled: bool
	floating_chat_enabled: bool
	summary_provider: str
	chat_default_provider: str
	allowed_roles: tuple
	max_rows: int
	max_groups: int
	max_context_chars: int
	rate_limit_per_hour: int

	def provider(self, name):
		return self.providers.get(name)

	def chat_providers(self):
		return [p.name for p in self.providers.values() if p.enabled and p.in_chat]

	def has_local(self):
		return any(p.enabled and p.is_local for p in self.providers.values())


def _override(fieldname):
	key = OVERRIDES.get(fieldname)
	if not key:
		return None
	value = frappe.conf.get(key)
	# `is None`, not falsiness: a configured 0 (e.g. spice_ai_ollama_enabled=0) is an override too.
	return value if value is not None else os.environ.get(key.upper())


def _flag(settings, fieldname):
	"""A checkbox setting, unless the deployment overrides it (e.g. spice_ai_ollama_enabled=0)."""
	value = _override(fieldname)
	if value is None or value == "":
		return bool(settings.get(fieldname))
	return str(value).strip().lower() not in ("0", "false", "no", "off")


def _secret(settings, fieldname):
	value = _override(fieldname)
	if value:
		return value
	if not settings.get(fieldname):
		return None
	return settings.get_password(fieldname, raise_exception=False)


def get_config() -> AIConfig:
	s = frappe.get_cached_doc(SETTINGS)
	providers = {
		OLLAMA: ProviderConfig(
			name=OLLAMA, enabled=_flag(s, "ollama_enabled"), model=s.ollama_model,
			base_url=(_override("ollama_url") or s.ollama_url or "").rstrip("/") or None,
			timeout=s.ollama_timeout or 300, in_chat=bool(s.ollama_in_chat), is_local=True,
			keep_alive=s.ollama_keep_alive or None, temperature=s.ollama_temperature or 0,
		),
		CLAUDE: ProviderConfig(
			name=CLAUDE, enabled=bool(s.claude_enabled), model=s.claude_model,
			base_url=(s.anthropic_base_url or "").rstrip("/") or None, timeout=s.claude_timeout or 60,
			in_chat=bool(s.claude_in_chat), is_local=False, api_key=_secret(s, "anthropic_api_key"),
		),
		OPENAI: ProviderConfig(
			name=OPENAI, enabled=bool(s.openai_enabled), model=s.openai_model,
			base_url=(s.openai_base_url or "").rstrip("/") or None, timeout=s.openai_timeout or 60,
			in_chat=bool(s.openai_in_chat), is_local=False, api_key=_secret(s, "openai_api_key"),
		),
	}
	local_enabled = any(p.enabled and p.is_local for p in providers.values())
	return AIConfig(
		# Summaries read patient records, so they exist only while a local model is available.
		providers=providers, summary_enabled=bool(s.summary_enabled) and local_enabled, chat_enabled=bool(s.chat_enabled),
		floating_chat_enabled=bool(s.floating_chat_enabled), summary_provider=s.summary_provider or OLLAMA,
		chat_default_provider=s.chat_default_provider or OLLAMA,
		allowed_roles=tuple(r.role for r in s.allowed_roles or []),
		max_rows=s.max_rows or 5000, max_groups=s.max_groups or 50,
		max_context_chars=s.max_context_chars or 12000, rate_limit_per_hour=s.rate_limit_per_hour or 60,
	)


def user_can_use_ai(user=None) -> bool:
	roles = get_config().allowed_roles
	user_roles = set(frappe.get_roles(user))
	return "System Manager" in user_roles or bool(roles and user_roles.intersection(roles))


def ensure_ai_access():
	if not user_can_use_ai():
		frappe.throw(frappe._("You are not allowed to use AI features."), frappe.PermissionError)
