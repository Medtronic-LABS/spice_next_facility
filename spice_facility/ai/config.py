"""The single typed accessor for AI configuration (SPICE AI Settings + site_config overrides).

Nothing else reads the settings doctype or secrets directly. A site_config / environment value wins over
the stored setting, so CI and deployments can inject credentials without touching the database.
"""

import os
from dataclasses import dataclass, field

import frappe

SETTINGS = "SPICE AI Settings"
OLLAMA, CLAUDE, OPENAI, GEMINI = "Ollama", "Claude", "OpenAI", "Gemini"
PROVIDER_NAMES = (OLLAMA, CLAUDE, OPENAI, GEMINI)
LOCAL_PROVIDERS = (OLLAMA,)
MODEL_DEFAULT = "Model default"  # a Select option meaning "send nothing, let the model decide"
# Patient check box: keep this patient's records away from cloud models (created by setup.install)
CLOUD_OPT_OUT_FIELD = "spice_no_cloud_ai"
# Settings fields of each provider start with this prefix (e.g. claude_enabled)
PROVIDER_FIELD_PREFIX = {OLLAMA: "ollama", CLAUDE: "claude", OPENAI: "openai", GEMINI: "gemini"}
# The settings field holding each cloud provider's API key
KEY_FIELDS = {CLAUDE: "anthropic_api_key", OPENAI: "openai_api_key", GEMINI: "gemini_api_key"}

# site_config / env keys that override stored values
OVERRIDES = {
	"ollama_enabled": "spice_ai_ollama_enabled",
	"ollama_url": "spice_ai_ollama_url",
	"anthropic_api_key": "spice_ai_anthropic_api_key",
	"openai_api_key": "spice_ai_openai_api_key",
	"gemini_api_key": "spice_ai_gemini_api_key",
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
	thinking: str | None = None  # Gemini thinking level ("Low", ...); None = the model's default


@dataclass(frozen=True)
class AIConfig:
	providers: dict
	summary_enabled: bool
	chat_enabled: bool
	floating_chat_enabled: bool
	summary_provider: str
	cloud_patient_data: bool  # cloud models may read patient records (summaries); off unless a site opts in
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


def has_override(fieldname) -> bool:
	"""Whether site_config / the environment supplies this setting (e.g. an API key injected by the deploy)."""
	return _override(fieldname) not in (None, "")


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
		GEMINI: ProviderConfig(
			name=GEMINI, enabled=bool(s.gemini_enabled), model=s.gemini_model,
			base_url=(s.gemini_base_url or "").rstrip("/") or None, timeout=s.gemini_timeout or 60,
			in_chat=bool(s.gemini_in_chat), is_local=False, api_key=_secret(s, "gemini_api_key"),
			thinking=None if (s.gemini_thinking or MODEL_DEFAULT) == MODEL_DEFAULT else s.gemini_thinking,
		),
	}
	cloud_patient_data = bool(s.cloud_patient_data_allowed)
	summarizer = providers.get(s.summary_provider or OLLAMA)
	summaries_possible = bool(summarizer and summarizer.enabled and (summarizer.is_local or cloud_patient_data))
	return AIConfig(
		# Summaries read patient records: they need their provider, and a cloud one needs the site's consent.
		providers=providers, summary_enabled=bool(s.summary_enabled) and summaries_possible,
		chat_enabled=bool(s.chat_enabled), cloud_patient_data=cloud_patient_data,
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
