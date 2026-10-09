from spice_facility.ai.providers.base import AIProviderError, LLMProvider, parse_json_object


class OllamaProvider(LLMProvider):
	"""Local Ollama. Structured output via `format: <JSON schema>`; `keep_alive` avoids cold reloads."""

	def _chat(self, system, messages, fmt=None, max_tokens=None):
		payload = {
			"model": self.model, "stream": False,
			"messages": [{"role": "system", "content": system}, *messages],
			"options": {"temperature": self.config.temperature},
		}
		if fmt is not None:
			payload["format"] = fmt
		if max_tokens:
			payload["options"]["num_predict"] = max_tokens
		if self.config.keep_alive:
			payload["keep_alive"] = self.config.keep_alive
		data = self._request("POST", f"{self.config.base_url}/api/chat", payload=payload)
		content = (data.get("message") or {}).get("content")
		if content is None:
			raise AIProviderError("Ollama returned no message")
		return content

	def complete_json(self, system, messages, schema):
		return parse_json_object(self._chat(system, messages, fmt=schema), self.name)

	def complete_text(self, system, messages, max_tokens=600):
		return self._chat(system, messages, max_tokens=max_tokens).strip()

	def list_models(self):
		data = self._request("GET", f"{self.config.base_url}/api/tags", timeout=10)
		return sorted(m["name"] for m in data.get("models", []))
