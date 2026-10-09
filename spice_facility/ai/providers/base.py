"""The contract every AI provider implements, and the errors it may raise."""

import json
from abc import ABC, abstractmethod

import requests

from spice_facility.ai.config import ProviderConfig


class AIProviderError(Exception):
	"""The provider answered, but not with something usable (HTTP error, bad JSON)."""


class AIProviderUnavailable(AIProviderError):
	"""The provider could not be reached or timed out."""


class LLMProvider(ABC):
	def __init__(self, config: ProviderConfig):
		self.config = config

	@property
	def name(self):
		return self.config.name

	@property
	def model(self):
		return self.config.model

	@property
	def is_local(self):
		return self.config.is_local

	@abstractmethod
	def complete_json(self, system: str, messages: list[dict], schema: dict) -> dict:
		"""Return an object that matches `schema`."""

	@abstractmethod
	def complete_text(self, system: str, messages: list[dict], max_tokens: int = 600) -> str:
		"""Return plain text."""

	@abstractmethod
	def list_models(self) -> list[str]:
		"""Models this provider can serve with the configured credentials."""

	# ─── shared HTTP plumbing ───────────────────────────────────────────────
	def _request(self, method, url, headers=None, payload=None, timeout=None):
		try:
			response = requests.request(method, url, headers=headers or {}, json=payload,
			                            timeout=timeout or self.config.timeout)
		except (requests.ConnectionError, requests.Timeout) as error:
			raise AIProviderUnavailable(f"{self.name} is not reachable at {url.split('?')[0]}") from error
		if response.status_code >= 400:
			raise AIProviderError(f"{self.name} returned HTTP {response.status_code}: {_short(response.text)}")
		try:
			return response.json()
		except ValueError as error:
			raise AIProviderError(f"{self.name} returned a non-JSON response") from error


def parse_json_object(text, provider):
	try:
		value = json.loads(text)
	except (TypeError, ValueError) as error:
		raise AIProviderError(f"{provider} did not return valid JSON") from error
	if not isinstance(value, dict):
		raise AIProviderError(f"{provider} returned JSON that is not an object")
	return value


def _short(text, limit=200):
	return (text or "").replace("\n", " ")[:limit]
