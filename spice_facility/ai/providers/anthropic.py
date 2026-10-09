from spice_facility.ai.providers.base import AIProviderError, LLMProvider

API_VERSION = "2023-06-01"
TOOL = "respond"


class ClaudeProvider(LLMProvider):
	"""Anthropic Messages API. Structured output by forcing a single tool whose input is the schema."""

	def _headers(self):
		return {"x-api-key": self.config.api_key or "", "anthropic-version": API_VERSION,
		        "content-type": "application/json"}

	def _messages(self, system, messages, max_tokens, tools=None, tool_choice=None):
		payload = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
		if tools:
			payload.update({"tools": tools, "tool_choice": tool_choice})
		return self._request("POST", f"{self.config.base_url}/v1/messages", self._headers(), payload)

	def complete_json(self, system, messages, schema):
		data = self._messages(system, messages, 2048,
		                      tools=[{"name": TOOL, "description": "Return the answer in this structure.",
		                              "input_schema": schema}],
		                      tool_choice={"type": "tool", "name": TOOL})
		for block in data.get("content", []):
			if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
				return block["input"]
		raise AIProviderError("Claude did not return the structured answer")

	def complete_text(self, system, messages, max_tokens=600):
		data = self._messages(system, messages, max_tokens)
		return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()

	def list_models(self):
		data = self._request("GET", f"{self.config.base_url}/v1/models", self._headers(), timeout=15)
		return sorted(m["id"] for m in data.get("data", []))
