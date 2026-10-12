from urllib.parse import urlencode

from spice_facility.ai.providers.base import AIProviderError, LLMProvider, parse_json_object

ROLES = {"user": "user", "assistant": "model"}
MAX_MODEL_PAGES = 10
# Thinking models (gemini-2.5+) spend output tokens on thinking first; this headroom keeps the answer's own
# budget (max_tokens) from being consumed by it.
THINKING_ALLOWANCE = 8192
# Structured output does not accept "" as an enum value, which our query plans use for "none": it is sent
# as this placeholder and mapped back in the answer.
EMPTY_ENUM = "(none)"


class GeminiProvider(LLMProvider):
	"""Google Gemini API (generateContent). Structured output via `responseJsonSchema`; the key travels in the
	`x-goog-api-key` header, never in the URL."""

	def _headers(self):
		return {"x-goog-api-key": self.config.api_key or "", "Content-Type": "application/json"}

	def _generate(self, system, messages, max_tokens, schema=None):
		# No temperature: Gemini 3 models are tuned for their default (1.0) and can loop at low values.
		generation = {"maxOutputTokens": max_tokens + THINKING_ALLOWANCE}
		if schema is not None:
			generation.update({"responseMimeType": "application/json", "responseJsonSchema": _encode_empty(schema)})
		payload = {
			"systemInstruction": {"parts": [{"text": system}]},
			"contents": [{"role": ROLES.get(m["role"], "user"), "parts": [{"text": m["content"]}]} for m in messages],
			"generationConfig": generation,
		}
		data = self._request("POST", f"{self.config.base_url}/models/{self.model}:generateContent",
		                     self._headers(), payload)
		candidate = (data.get("candidates") or [{}])[0]
		parts = (candidate.get("content") or {}).get("parts")
		if not parts:
			reason = (candidate.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason")
			          or "no candidate")
			raise AIProviderError(f"Gemini returned no answer ({reason})")
		text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
		if candidate.get("finishReason") == "MAX_TOKENS":
			raise AIProviderError("Gemini stopped before finishing the answer (MAX_TOKENS)")
		return text

	def complete_json(self, system, messages, schema):
		return _decode_empty(parse_json_object(self._generate(system, messages, 2048, schema), self.name))

	def complete_text(self, system, messages, max_tokens=600):
		return self._generate(system, messages, max_tokens).strip()

	def list_models(self):
		"""Models that can generate content, without the `models/` prefix the API puts on their names."""
		models, token = [], None
		for _ in range(MAX_MODEL_PAGES):
			query = urlencode({"pageSize": 1000, **({"pageToken": token} if token else {})})
			data = self._request("GET", f"{self.config.base_url}/models?{query}", self._headers(), timeout=15)
			models += [m["name"].removeprefix("models/") for m in data.get("models", [])
			           if "generateContent" in m.get("supportedGenerationMethods", [])]
			token = data.get("nextPageToken")
			if not token:
				break
		return sorted(models)


def _encode_empty(schema):
	"""A copy of `schema` with every "" enum value replaced by EMPTY_ENUM."""
	if isinstance(schema, list):
		return [_encode_empty(item) for item in schema]
	if not isinstance(schema, dict):
		return schema
	encoded = {key: _encode_empty(value) for key, value in schema.items()}
	if isinstance(schema.get("enum"), list):
		encoded["enum"] = [EMPTY_ENUM if value == "" else value for value in schema["enum"]]
	return encoded


def _decode_empty(value):
	if value == EMPTY_ENUM:
		return ""
	if isinstance(value, list):
		return [_decode_empty(item) for item in value]
	if isinstance(value, dict):
		return {key: _decode_empty(item) for key, item in value.items()}
	return value
