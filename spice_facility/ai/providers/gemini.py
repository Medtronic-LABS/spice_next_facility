from spice_facility.ai.providers.base import AIProviderError, LLMProvider, parse_json_object

ROLES = {"user": "user", "assistant": "model"}
MAX_MODEL_PAGES = 10


class GeminiProvider(LLMProvider):
	"""Google Gemini API (generateContent). Structured output via `responseJsonSchema`; the key travels in the
	`x-goog-api-key` header, never in the URL."""

	def _headers(self):
		return {"x-goog-api-key": self.config.api_key or "", "Content-Type": "application/json"}

	def _generate(self, system, messages, max_tokens, schema=None):
		generation = {"maxOutputTokens": max_tokens, "temperature": self.config.temperature}
		if schema is not None:
			generation.update({"responseMimeType": "application/json", "responseJsonSchema": schema})
		payload = {
			"systemInstruction": {"parts": [{"text": system}]},
			"contents": [{"role": ROLES.get(m["role"], "user"), "parts": [{"text": m["content"]}]} for m in messages],
			"generationConfig": generation,
		}
		data = self._request("POST", f"{self.config.base_url}/models/{self.model}:generateContent",
		                     self._headers(), payload)
		try:
			parts = data["candidates"][0]["content"]["parts"]
		except (KeyError, IndexError, TypeError) as error:
			reason = (data.get("promptFeedback") or {}).get("blockReason") or "no candidate"
			raise AIProviderError(f"Gemini returned no answer ({reason})") from error
		return "".join(p.get("text", "") for p in parts if not p.get("thought"))

	def complete_json(self, system, messages, schema):
		return parse_json_object(self._generate(system, messages, 2048, schema), self.name)

	def complete_text(self, system, messages, max_tokens=600):
		return self._generate(system, messages, max_tokens).strip()

	def list_models(self):
		"""Models that can generate content, without the `models/` prefix the API puts on their names."""
		models, token = [], None
		for _ in range(MAX_MODEL_PAGES):
			query = "?pageSize=1000" + (f"&pageToken={token}" if token else "")
			data = self._request("GET", f"{self.config.base_url}/models{query}", self._headers(), timeout=15)
			models += [m["name"].removeprefix("models/") for m in data.get("models", [])
			           if "generateContent" in m.get("supportedGenerationMethods", [])]
			token = data.get("nextPageToken")
			if not token:
				break
		return sorted(models)
