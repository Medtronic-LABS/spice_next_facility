from spice_facility.ai.providers.base import AIProviderError, LLMProvider, parse_json_object


class OpenAIProvider(LLMProvider):
	"""OpenAI or any OpenAI-compatible endpoint. Structured output via `response_format: json_schema`."""

	def _headers(self):
		return {"Authorization": f"Bearer {self.config.api_key or ''}", "Content-Type": "application/json"}

	def _chat(self, system, messages, max_tokens, response_format=None):
		payload = {"model": self.model, "max_tokens": max_tokens,
		           "messages": [{"role": "system", "content": system}, *messages]}
		if response_format:
			payload["response_format"] = response_format
		data = self._request("POST", f"{self.config.base_url}/chat/completions", self._headers(), payload)
		try:
			return data["choices"][0]["message"]["content"]
		except (KeyError, IndexError, TypeError) as error:
			raise AIProviderError("OpenAI returned no message") from error

	def complete_json(self, system, messages, schema):
		content = self._chat(system, messages, 2048, {"type": "json_schema",
		                                               "json_schema": {"name": "respond", "schema": schema}})
		return parse_json_object(content, self.name)

	def complete_text(self, system, messages, max_tokens=600):
		return (self._chat(system, messages, max_tokens) or "").strip()

	def list_models(self):
		data = self._request("GET", f"{self.config.base_url}/models", self._headers(), timeout=15)
		return sorted(m["id"] for m in data.get("data", []))
