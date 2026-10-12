"""A scripted model for end-to-end tests: answers Ask Data's planning calls from a small table, without a network.

Configured only when config.fake_provider_allowed() (a site that allows tests and sets spice_ai_fake_provider in
its site config).
Questions containing SLOW_MARKER wait SLOW_SECONDS while planning, so a test can stop a question that is running.
"""

import time

from spice_facility.ai.providers.base import LLMProvider

SLOW_MARKER = "[slow]"
SLOW_SECONDS = 20

# keyword in the question -> (dataset, group_by, output, time_bucket); the first match wins, the last is the default.
# One entry per visual the chat draws: ranked bars, pie, donut, line and column trends, a single number.
SCRIPT = (
	("as a pie", ("Lab Test", "status", "pie", "none")),
	("as a donut", ("Patient Encounter", "medical_department", "donut", "none")),
	("monthly bars", ("Patient Encounter", "", "bar", "month")),
	("per month", ("Patient Encounter", "", "line", "month")),
	("lab", ("Lab Test", "status", "bar", "none")),
	("department", ("Patient Encounter", "medical_department", "bar", "none")),
	("how many", ("Patient Encounter", "", "number", "none")),
	("", ("Patient Encounter", "status", "bar", "none")),
)


def _question(messages):
	content = (messages[-1] or {}).get("content", "") if messages else ""
	return content.rsplit("Question:", 1)[-1].strip().lower()


def _scripted(question):
	return next(answer for keyword, answer in SCRIPT if keyword in question)


class FakeProvider(LLMProvider):
	def complete_json(self, system, messages, schema):
		question = _question(messages)
		properties = (schema or {}).get("properties", {})
		dataset, group_by, output, bucket = _scripted(question)
		if "mode" in properties:
			return {"mode": "data"}
		if set(properties) == {"dataset"}:
			if SLOW_MARKER in question:
				time.sleep(SLOW_SECONDS)
			return {"dataset": dataset}
		return {"filters": [], "date_range": {"preset": "none", "from": None, "to": None}, "group_by": group_by,
		        "time_bucket": bucket, "measure": {"agg": "count", "field": ""}, "sort": "value_desc", "output": output,
		        "list_fields": [], "title": f"{dataset} (fake)"}

	def complete_text(self, system, messages, max_tokens=600):
		return "Scripted answer from the fake model."

	def list_models(self):
		return ["FAKE"]
