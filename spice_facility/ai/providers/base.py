"""The contract every AI provider implements, and the errors it may raise."""

import json
import time
from abc import ABC, abstractmethod

import requests

from spice_facility.ai.config import ProviderConfig

# A busy or briefly failing provider (rate limit, overload, gateway) is retried a couple of times with a short
# backoff; anything else, including a timeout (the wait was already long), fails at once.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAYS = (1, 2)
MAX_RETRY_AFTER = 5


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
		for delay in (*RETRY_DELAYS, None):
			try:
				response = requests.request(method, url, headers=headers or {}, json=payload,
				                            timeout=timeout or self.config.timeout)
			except requests.Timeout as error:
				raise AIProviderUnavailable(f"{self.name} did not answer within {timeout or self.config.timeout} s "
				                            f"({url.split('?')[0]})") from error
			except requests.ConnectionError as error:
				if delay is None:
					raise AIProviderUnavailable(f"{self.name} is not reachable at {url.split('?')[0]}") from error
				time.sleep(delay)
				continue
			except requests.RequestException as error:  # e.g. a missing or malformed API base URL
				raise AIProviderUnavailable(f"{self.name} cannot be called at {url.split('?')[0]}") from error
			if response.status_code in RETRY_STATUSES and delay is not None:
				time.sleep(_retry_after(response, delay))
				continue
			break
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


def _retry_after(response, default):
	try:
		return min(float(response.headers.get("Retry-After", default)), MAX_RETRY_AFTER)
	except (TypeError, ValueError):
		return default


def _short(text, limit=200):
	return (text or "").replace("\n", " ")[:limit]
