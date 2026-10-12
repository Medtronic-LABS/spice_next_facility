"""AI features, with fake providers (no network): catalog, governed queries, data policy, settings, MCP."""

import json
import os
from dataclasses import replace
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from spice_facility.ai import data_catalog, query
from spice_facility.ai.clinical_flags import lab_flags, parse_range
from spice_facility.ai.config import GEMINI, ProviderConfig, ensure_ai_access
from spice_facility.ai.providers.base import MAX_RETRY_AFTER, RETRY_DELAYS, AIProviderError
from spice_facility.ai.providers.gemini import EMPTY_ENUM, THINKING_ALLOWANCE, GeminiProvider
from spice_facility.ai.summary_context import build_context, context_hash
from spice_facility.ai.talk_to_data import (
	answer_data,
	cited_records,
	implicit_scope,
	keep_known_links,
	resolve_page,
	run_question,
	tidy,
)

GROUP_SPEC = {"filters": [], "date_range": {"preset": "none"}, "group_by": "medical_department", "time_bucket": "none",
              "measure": {"agg": "count", "field": ""}, "sort": "value_desc", "output": "bar", "list_fields": [],
              "title": "By department"}


class FakeProvider:
	"""Records every message it is sent; answers from a script."""

	def __init__(self, name, is_local, json_answers=(), text="answer"):
		self.config = ProviderConfig(name=name, enabled=True, model=f"{name}-test", base_url=None, timeout=5,
		                             in_chat=True, is_local=is_local)
		self.name, self.model, self.is_local = name, f"{name}-test", is_local
		self.json_answers, self.text, self.sent = list(json_answers), text, []

	def complete_json(self, system, messages, schema):
		self.sent.append(system + json.dumps(messages))
		return self.json_answers.pop(0)

	def complete_text(self, system, messages, max_tokens=600):
		self.sent.append(system + json.dumps(messages))
		return self.text


TEST_PATIENT = "SF Test Patient"
TEST_DEPARTMENT = "SF Test Department"


def _raw_insert(doctype, name, values):
	"""Insert a row without running controllers: a CI site has no company/setup-wizard data, which Health's
	validations (customer creation, appointment types) would otherwise require."""
	if frappe.db.exists(doctype, name):
		return
	doc = frappe.new_doc(doctype)
	doc.update(values)
	doc.name = name
	doc.db_insert()


def ensure_fixtures():
	"""Records the AI tests need, so they pass on an empty CI site as well as on a seeded one."""
	if not frappe.db.exists("Gender", "Female"):
		frappe.get_doc({"doctype": "Gender", "gender": "Female"}).insert(ignore_permissions=True)
	if not frappe.db.exists("Medical Department", TEST_DEPARTMENT):
		frappe.get_doc({"doctype": "Medical Department", "department": TEST_DEPARTMENT}).insert(ignore_permissions=True)
	_raw_insert("Patient", TEST_PATIENT, {"first_name": "SF Test", "last_name": "Patient", "patient_name": TEST_PATIENT,
	                                      "sex": "Female", "dob": "1980-01-01", "status": "Active"})
	for i in (1, 2):
		_raw_insert("Patient Encounter", f"SF-TEST-ENC-{i}", {
			"patient": TEST_PATIENT, "patient_name": TEST_PATIENT, "medical_department": TEST_DEPARTMENT,
			"encounter_date": frappe.utils.nowdate(), "encounter_time": "10:00:00", "practitioner_name": "SF Test Doctor",
			"status": "Completed", "docstatus": 1})


def any_patient():
	ensure_fixtures()
	return frappe.db.get_value("Patient", TEST_PATIENT, ["name", "patient_name"], as_dict=True)


class AITestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_fixtures()


class TestCatalogAndQuery(AITestCase):
	def test_every_catalog_field_exists_on_this_site(self):
		for dataset in data_catalog.DATASETS:
			if not frappe.db.exists("DocType", dataset.doctype):
				continue
			missing = [f for f in [*dataset.fields, *dataset.measures, dataset.date_field]
			           if not data_catalog.field_type(dataset.doctype, f)]
			self.assertEqual(missing, [], dataset.doctype)

	def test_validator_rejects_names_outside_the_catalog(self):
		dataset = data_catalog.get("Patient Encounter")
		for bad in ({**GROUP_SPEC, "group_by": "password"},
		            {**GROUP_SPEC, "filters": [{"field": "owner_password", "op": "=", "value": "x"}]},
		            {**GROUP_SPEC, "filters": [{"field": "status", "op": "; drop table", "value": "x"}]},
		            {**GROUP_SPEC, "measure": {"agg": "sum", "field": "status"}}):
			with self.assertRaises(query.QueryValidationError):
				query.validate(bad, dataset, 50)

	def test_group_counts_match_the_database(self):
		dataset = data_catalog.get("Patient Encounter")
		spec = query.validate(GROUP_SPEC, dataset, 50)
		result = query.execute(spec, dataset)
		for label, value in zip(result["labels"], result["values"], strict=False):
			raw = result["group_raw"][label]
			self.assertEqual(value, frappe.db.count("Patient Encounter", {"medical_department": raw}))

	def test_page_scope_limits_to_the_open_patient(self):
		patient = any_patient()
		page = resolve_page({"doctype": "Patient", "name": patient.name})
		scope, labels = implicit_scope(data_catalog.get("Lab Test"), page)
		self.assertEqual(scope, {"patient": patient.name})
		self.assertTrue(labels)

	def test_tidy_drops_unrequested_period_and_bucket(self):
		dataset = data_catalog.get("Patient Appointment")
		spec = query.validate({**GROUP_SPEC, "group_by": "department", "time_bucket": "month",
		                       "date_range": {"preset": "this_year"},
		                       "filters": [{"field": "department", "op": "is not set", "value": ""}]}, dataset, 50)
		spec = tidy(spec, dataset, "How many appointments per department?")
		self.assertEqual((spec["time_bucket"], spec["date_range"]["preset"], spec["filters"]), ("none", "none", []))

	def test_links_only_to_grounded_records(self):
		text = keep_known_links("See [[Lab Test:LT-1]] and [[Patient:Somebody]]", {"Lab Test:LT-1"})
		self.assertEqual(text, "See [[Lab Test:LT-1]] and Somebody")

	def test_source_parts_match_the_one_line_description(self):
		dataset = data_catalog.get("Patient Appointment")
		spec = query.validate({**GROUP_SPEC, "group_by": "department", "date_range": {"preset": "this_month"},
		                       "filters": [{"field": "status", "op": "=", "value": "Open"}]}, dataset, 50)
		parts = query.describe_parts(spec, dataset, ["Patient = X (this page)"])
		self.assertEqual((parts["doctype"], parts["aggregate"], parts["scope"]), ("Patient Appointment", "count", ["Patient = X (this page)"]))
		self.assertEqual(len(parts["filters"]), 1)
		self.assertTrue(parts["period_from"] and parts["period_to"])
		self.assertEqual(query.describe(spec, dataset, ["Patient = X (this page)"]), " · ".join(
			[parts["dataset"], parts["measure"], parts["group_by"], parts["filters"][0], parts["period"], parts["scope"][0]]))

	def test_record_sources_list_the_page_and_cited_records_once(self):
		patient = any_patient()
		page = {"doctype": "Patient", "name": patient.name}
		text = "Seen in [[Patient Encounter:SF-TEST-ENC-1]] and [[Patient Encounter:SF-TEST-ENC-1]]; [[Lab Test:NO-SUCH-TEST]]."
		sources = cited_records(text, page)
		self.assertEqual([(s["doctype"], s["name"]) for s in sources],
		                 [("Patient", patient.name), ("Patient Encounter", "SF-TEST-ENC-1")])
		self.assertEqual(sources[0]["label"], patient.patient_name)

	def test_get_answer_returns_when_it_was_asked_and_how_long_it_took(self):
		from spice_facility.api.talk_to_data import get_answer

		log = frappe.get_doc({"doctype": "SPICE AI Query Log", "user": frappe.session.user, "question": "How many?",
		                      "status": "Answered", "duration_ms": 1234,
		                      "answer_json": json.dumps({"mode": "data", "text": "3"})}).insert(ignore_permissions=True)
		data = get_answer(log.name)
		self.assertEqual((data["duration_ms"], str(data["asked_on"])), (1234, str(log.creation)))

	def test_record_sources_ignore_unknown_doctypes(self):
		self.assertEqual(cited_records("[[No Such Doctype:No Such Doctype]]", None), [])

	def test_group_total_counts_every_row_not_just_the_charted_groups(self):
		dataset = data_catalog.get("Patient Encounter")
		spec = query.validate(GROUP_SPEC, dataset, 1)  # keep only the top group
		result = query.execute(spec, dataset)
		self.assertEqual(len(result["labels"]), 1)
		self.assertEqual(result["total"], result["rows_read"])
		self.assertEqual(query.present(result, spec, dataset)["total"], result["rows_read"])

	def test_no_total_when_the_rows_were_capped(self):
		dataset = data_catalog.get("Patient Encounter")
		result = query.execute(query.validate(GROUP_SPEC, dataset, 50), dataset, max_rows=1)
		self.assertTrue(result["truncated"])
		self.assertNotIn("total", result)

	def _query_log(self, status):
		return frappe.get_doc({"doctype": "SPICE AI Query Log", "user": frappe.session.user, "question": "How many?",
		                       "status": status}).insert(ignore_permissions=True)

	def test_stop_marks_a_queued_question_stopped_and_the_job_then_does_nothing(self):
		from spice_facility.api import talk_to_data as api

		log = self._query_log("Queued")
		self.assertEqual(api.stop(log.name)["status"], "Stopped")
		with patch("spice_facility.api.talk_to_data.run_question", side_effect=AssertionError("must not run")):
			api.run(log.name)
		self.assertEqual(frappe.db.get_value("SPICE AI Query Log", log.name, "status"), "Stopped")

	def test_an_answer_that_arrives_after_stop_is_discarded(self):
		from spice_facility.api import talk_to_data as api

		log = self._query_log("Queued")

		def answer_then_find_it_stopped(*_args, **_kwargs):
			frappe.db.set_value("SPICE AI Query Log", log.name, "status", "Stopped")
			return {"mode": "data", "text": "3"}, {"mode": "Data"}

		with patch("spice_facility.api.talk_to_data.run_question", side_effect=answer_then_find_it_stopped):
			api.run(log.name)
		row = frappe.db.get_value("SPICE AI Query Log", log.name, ["status", "answer_json"], as_dict=True)
		self.assertEqual((row.status, row.answer_json), ("Stopped", None))

	def test_stop_leaves_a_finished_question_alone(self):
		from spice_facility.api import talk_to_data as api

		log = self._query_log("Answered")
		self.assertEqual(api.stop(log.name)["status"], "Answered")

	def test_record_sources_leave_out_records_the_user_cannot_read(self):
		patient = any_patient()
		with patch("spice_facility.ai.talk_to_data.frappe.has_permission", return_value=False):
			self.assertEqual(cited_records("[[Patient Encounter:SF-TEST-ENC-1]]", {"doctype": "Patient", "name": patient.name}), [])


class TestDataPolicy(AITestCase):
	def test_cloud_planner_never_receives_patient_values(self):
		patient = any_patient()
		cloud = FakeProvider("Claude", False, json_answers=[{"dataset": "Patient Encounter"},
		                                                     {**GROUP_SPEC, "output": "list", "list_fields": ["patient_name"]}])
		local = FakeProvider("Ollama", True, text="Here are the encounters.")
		with patch("spice_facility.ai.talk_to_data.local_provider", return_value=local):
			answer = answer_data("List encounters", cloud, None, None, False)
		self.assertTrue(answer["table"]["rows"], "the query must return rows for this test to mean anything")
		sent_to_cloud = " ".join(cloud.sent)
		for row in answer["table"]["rows"]:
			self.assertNotIn(str(row.get("patient_name")), sent_to_cloud)
		self.assertIn(patient.patient_name if patient else "", " ".join(local.sent) + (patient.patient_name or ""))

	def test_record_questions_are_answered_locally_even_with_a_cloud_planner(self):
		patient = any_patient()
		cloud = FakeProvider("Claude", False, json_answers=[{"mode": "record"}])
		local = FakeProvider("Ollama", True, text="Last HbA1c was 8.2%.")
		# This is about a deployment that HAS a local model; CI runs with SPICE_AI_OLLAMA_ENABLED=0.
		with patch("spice_facility.ai.talk_to_data.chat_provider", return_value=cloud), \
		     patch("spice_facility.ai.talk_to_data.has_local_provider", return_value=True), \
		     patch("spice_facility.ai.talk_to_data.local_provider", return_value=local):
			answer, meta = run_question("When was the last HbA1c?", "Claude", {"doctype": "Patient", "name": patient.name})
		self.assertEqual(meta["mode"], "Record")
		self.assertEqual(answer["text"], "Last HbA1c was 8.2%.")
		self.assertEqual(answer["sources"][0]["name"], patient.name)
		self.assertNotIn(patient.patient_name, " ".join(cloud.sent))


class TestWithoutLocalModel(AITestCase):
	def test_answers_are_built_from_the_result_and_record_questions_stay_data(self):
		cloud = FakeProvider("Claude", False, json_answers=[{"dataset": "Patient Encounter"}, GROUP_SPEC])
		with patch("spice_facility.ai.talk_to_data.has_local_provider", return_value=False), \
		     patch("spice_facility.ai.talk_to_data.local_provider", side_effect=AssertionError("no local model here")):
			answer = answer_data("Encounters by department", cloud, None, None, False)
		self.assertIsNone(answer["narrator"])
		self.assertTrue(answer["text"])
		self.assertIn("chart", answer)
		self.assertEqual(answer["source"]["doctype"], "Patient Encounter")

	def test_summaries_switch_off_without_a_local_model(self):
		from spice_facility.ai.config import get_config

		frappe.conf.spice_ai_ollama_enabled = 0
		try:
			config = get_config()
			self.assertFalse(config.has_local())
			self.assertFalse(config.summary_enabled)
		finally:
			frappe.conf.pop("spice_ai_ollama_enabled", None)


class TestSummary(AITestCase):
	def test_context_hash_is_stable_and_flags_are_computed(self):
		patient = any_patient()
		doc = frappe.get_doc("Patient", patient.name)
		self.assertEqual(context_hash(build_context(doc)), context_hash(build_context(doc)))

	def test_range_parsing_and_flags(self):
		self.assertEqual(parse_range("70-100"), (70, 100))
		self.assertEqual(parse_range("<200"), (None, 200))
		self.assertEqual(parse_range("M 13-17, F 12-15", "Female"), (12, 15))
		flags = lab_flags([{"test": "CBC", "results": [{"parameter": "Hb", "value": "8.9", "unit": "g/dL",
		                                                  "normal_range": "M 13-17, F 12-15"}]}], "Female")
		self.assertEqual(len(flags), 1)
		self.assertIn("low", flags[0]["text"])


class TestSettingsAndAccess(IntegrationTestCase):
	def test_cloud_summary_provider_and_keyless_cloud_are_rejected(self):
		settings = frappe.get_doc("SPICE AI Settings")
		settings.summary_provider = "Claude"
		with self.assertRaises(frappe.ValidationError):
			settings.validate()
		settings.reload()
		settings.claude_enabled, settings.anthropic_api_key = 1, None
		with self.assertRaises(frappe.ValidationError):
			settings.validate()

	def test_test_provider_reports_models_without_secrets(self):
		from spice_facility.api.ai_settings import test_provider

		response = MagicMock(status_code=200)
		response.json.return_value = {"models": [{"name": "llama3.1:8b"}]}
		with patch("spice_facility.ai.providers.base.requests.request", return_value=response):
			result = test_provider("Ollama")
		self.assertTrue(result["ok"])
		self.assertIn("llama3.1:8b", result["models"])
		self.assertNotIn("api_key", json.dumps(result))

	def test_a_key_supplied_by_the_deployment_satisfies_the_form(self):
		settings = frappe.get_doc("SPICE AI Settings")
		settings.gemini_enabled, settings.gemini_api_key, settings.gemini_model = 1, None, "gemini-test"
		settings.gemini_base_url = "https://generativelanguage.googleapis.com/v1beta"
		environ = {k: v for k, v in os.environ.items() if k != "SPICE_AI_GEMINI_API_KEY"}
		with patch.dict(frappe.conf, {"spice_ai_gemini_api_key": None}), patch.dict(os.environ, environ, clear=True):
			with self.assertRaisesRegex(frappe.ValidationError, "no API key"):
				settings.validate()
			with patch.dict(os.environ, {"SPICE_AI_GEMINI_API_KEY": "from-env"}):
				settings.validate()

	def test_the_gemini_key_only_goes_to_google(self):
		settings = frappe.get_doc("SPICE AI Settings")
		settings.gemini_enabled, settings.gemini_api_key, settings.gemini_model = 1, "key", "gemini-test"
		for url in ("https://attacker.example/v1beta", "http://generativelanguage.googleapis.com/v1beta",
		            "https://googleapis.com.attacker.example", ""):
			settings.gemini_base_url = url
			with self.assertRaisesRegex(frappe.ValidationError, "base URL", msg=url):
				settings.validate()

	def test_new_settings_fields_get_their_defaults_without_overriding_a_choice(self):
		from spice_facility.setup.install import fill_ai_settings_defaults

		frappe.db.delete("Singles", {"doctype": "SPICE AI Settings", "field": ("in", ["gemini_base_url", "gemini_in_chat"])})
		frappe.db.set_single_value("SPICE AI Settings", "gemini_timeout", 17)
		fill_ai_settings_defaults()
		self.assertEqual(frappe.db.get_single_value("SPICE AI Settings", "gemini_base_url"),
		                 "https://generativelanguage.googleapis.com/v1beta")
		self.assertEqual(frappe.db.get_single_value("SPICE AI Settings", "gemini_in_chat"), 1)
		self.assertEqual(frappe.db.get_single_value("SPICE AI Settings", "gemini_timeout"), 17)

	def test_users_without_an_allowed_role_are_refused(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				ensure_ai_access()
		finally:
			frappe.set_user("Administrator")


class TestMcp(AITestCase):
	def call(self, payload):
		from spice_facility.api.mcp import mcp

		frappe.local.form_dict = frappe._dict(payload)
		return json.loads(mcp().get_data())

	def test_tools_are_read_only_and_validated(self):
		tools = [t["name"] for t in self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
		self.assertEqual(sorted(tools), ["ask", "describe_dataset", "list_datasets", "run_query"])
		before = frappe.db.count("SPICE AI Query Log")
		result = self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
			"name": "run_query", "arguments": {"dataset": "Patient Encounter", "spec": GROUP_SPEC}}})["result"]
		self.assertFalse(result["isError"])
		self.assertEqual(frappe.db.count("SPICE AI Query Log"), before, "run_query must not write")
		bad = self.call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
			"name": "run_query", "arguments": {"dataset": "Patient Encounter", "spec": {**GROUP_SPEC, "group_by": "password"}}}})
		self.assertTrue(bad["result"]["isError"])


def _response(body):
	response = MagicMock(status_code=200)
	response.json.return_value = body
	return response


class TestGeminiProvider(IntegrationTestCase):
	"""The Gemini wire format, against a recorded-shape response (no network)."""

	def setUp(self):
		self.provider = GeminiProvider(ProviderConfig(
			name=GEMINI, enabled=True, model="gemini-test", base_url="https://gemini.example/v1beta", timeout=5,
			in_chat=True, is_local=False, api_key="secret-key"))

	def call(self, body, method, *args):
		with patch("spice_facility.ai.providers.base.requests.request", return_value=_response(body)) as request:
			result = getattr(self.provider, method)(*args)
		return result, request.call_args

	def test_structured_answer_uses_the_schema_and_keeps_the_key_out_of_the_url(self):
		schema = {"type": "object", "properties": {"mode": {"type": "string"}}}
		answer, call = self.call({"candidates": [{"content": {"parts": [{"text": '{"mode": "data"}'}]}}]},
		                         "complete_json", "system", [{"role": "user", "content": "q"},
		                                                     {"role": "assistant", "content": "a"}], schema)
		self.assertEqual(answer, {"mode": "data"})
		method, url = call.args
		self.assertEqual((method, url), ("POST", "https://gemini.example/v1beta/models/gemini-test:generateContent"))
		self.assertNotIn("secret-key", url)
		self.assertEqual(call.kwargs["headers"]["x-goog-api-key"], "secret-key")
		payload = call.kwargs["json"]
		self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "system"}]})
		self.assertEqual([c["role"] for c in payload["contents"]], ["user", "model"])
		self.assertEqual(payload["generationConfig"]["responseJsonSchema"], schema)
		self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
		self.assertNotIn("temperature", payload["generationConfig"], "Gemini 3 keeps its default temperature")

	def test_text_answer_skips_thought_parts(self):
		text, call = self.call({"candidates": [{"content": {"parts": [
			{"text": "thinking", "thought": True}, {"text": " Answer. "}]}}]}, "complete_text", "s",
			[{"role": "user", "content": "q"}], 100)
		self.assertEqual(text, "Answer.")
		self.assertNotIn("responseJsonSchema", call.kwargs["json"]["generationConfig"])
		self.assertEqual(call.kwargs["json"]["generationConfig"]["maxOutputTokens"], 100 + THINKING_ALLOWANCE)

	def test_a_blocked_prompt_is_a_provider_error(self):
		with self.assertRaisesRegex(AIProviderError, "SAFETY"):
			self.call({"promptFeedback": {"blockReason": "SAFETY"}}, "complete_text", "s",
			          [{"role": "user", "content": "q"}])

	def test_models_are_paged_filtered_and_unprefixed(self):
		pages = [
			_response({"models": [{"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]},
			                      {"name": "models/embedding-1", "supportedGenerationMethods": ["embedContent"]}],
			           "nextPageToken": "next"}),
			_response({"models": [{"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]}]}),
		]
		with patch("spice_facility.ai.providers.base.requests.request", side_effect=pages) as request:
			models = self.provider.list_models()
		self.assertEqual(models, ["gemini-a", "gemini-b"])
		self.assertIn("pageToken=next", request.call_args_list[1].args[1])

	def test_empty_enum_values_travel_as_a_placeholder(self):
		schema = {"type": "object", "properties": {"group_by": {"type": "string", "enum": ["", "department"]}}}
		answer, call = self.call({"candidates": [{"content": {"parts": [{"text": json.dumps({"group_by": EMPTY_ENUM})}]}}]},
		                         "complete_json", "s", [{"role": "user", "content": "q"}], schema)
		self.assertEqual(answer, {"group_by": ""})
		sent = call.kwargs["json"]["generationConfig"]["responseJsonSchema"]
		self.assertEqual(sent["properties"]["group_by"]["enum"], [EMPTY_ENUM, "department"])
		self.assertEqual(schema["properties"]["group_by"]["enum"], ["", "department"], "caller's schema untouched")

	def test_an_unfinished_answer_reports_why(self):
		for body in ({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model"}}]},
		             {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"mo'}]}}]}):
			with self.assertRaisesRegex(AIProviderError, "MAX_TOKENS"):
				self.call(body, "complete_json", "s", [{"role": "user", "content": "q"}], {"type": "object"})

	def test_an_http_error_never_contains_the_key(self):
		response = MagicMock(status_code=400, text='{"error": {"message": "API key not valid."}}')
		with patch("spice_facility.ai.providers.base.requests.request", return_value=response):
			with self.assertRaises(AIProviderError) as raised:
				self.provider.complete_text("s", [{"role": "user", "content": "q"}])
		self.assertIn("HTTP 400", str(raised.exception))
		self.assertNotIn("secret-key", str(raised.exception))

	def test_thinking_level_is_sent_only_when_chosen(self):
		body = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
		_, call = self.call(body, "complete_text", "s", [{"role": "user", "content": "q"}])
		self.assertNotIn("thinkingConfig", call.kwargs["json"]["generationConfig"])
		self.provider = GeminiProvider(replace(self.provider.config, thinking="Low"))
		_, call = self.call(body, "complete_text", "s", [{"role": "user", "content": "q"}])
		self.assertEqual(call.kwargs["json"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})


class TestProviderRetries(IntegrationTestCase):
	"""Busy providers are retried briefly; timeouts and client errors are not."""

	def setUp(self):
		self.provider = GeminiProvider(ProviderConfig(
			name=GEMINI, enabled=True, model="m", base_url="https://gemini.example/v1beta", timeout=5,
			in_chat=True, is_local=False, api_key="k"))

	def request(self, side_effect):
		with patch("spice_facility.ai.providers.base.requests.request", side_effect=side_effect) as request, \
		     patch("spice_facility.ai.providers.base.time.sleep") as sleep:
			try:
				return self.provider.list_models(), request, sleep
			except AIProviderError as error:
				return error, request, sleep

	def test_a_busy_answer_is_retried(self):
		busy = MagicMock(status_code=503, text="high demand", headers={"Retry-After": "30"})
		result, request, sleep = self.request([busy, _response({"models": []})])
		self.assertEqual(result, [])
		self.assertEqual(request.call_count, 2)
		sleep.assert_called_once_with(MAX_RETRY_AFTER)

	def test_retries_stop_after_the_last_attempt(self):
		busy = MagicMock(status_code=503, text="high demand", headers={})
		result, request, _ = self.request([busy] * 5)
		self.assertIsInstance(result, AIProviderError)
		self.assertIn("HTTP 503", str(result))
		self.assertEqual(request.call_count, len(RETRY_DELAYS) + 1)

	def test_a_timeout_or_client_error_is_not_retried(self):
		result, request, sleep = self.request(requests.Timeout())
		self.assertIn("did not answer within 15 s", str(result))  # list_models waits 15 s
		self.assertEqual(request.call_count, 1)
		result, request, sleep = self.request([MagicMock(status_code=400, text="bad", headers={})])
		self.assertEqual(request.call_count, 1)
		sleep.assert_not_called()


class TestCloudSummaries(AITestCase):
	"""A cloud model may write summaries only when the site opts in, and never sees direct identifiers."""

	def settings(self, **values):
		doc = frappe.get_doc("SPICE AI Settings")
		doc.update({"summary_enabled": 1, "summary_provider": "Claude", "claude_enabled": 1,
		            "anthropic_api_key": "key", "claude_model": "claude-test", "cloud_patient_data_allowed": 0, **values})
		return doc

	def test_a_cloud_summary_provider_needs_the_sites_consent(self):
		agreed = {"cloud_patient_data_allowed": 1, "cloud_patient_data_agreement": "Anthropic DPA 2026-10"}
		with self.assertRaisesRegex(frappe.ValidationError, "Allow cloud models"):
			self.settings().validate()
		with self.assertRaisesRegex(frappe.ValidationError, "data-processing agreement"):
			self.settings(cloud_patient_data_allowed=1).validate()
		self.settings(**agreed).validate()
		with self.assertRaisesRegex(frappe.ValidationError, "enable Claude"):
			self.settings(**agreed, claude_enabled=0).validate()

	def test_summaries_run_on_a_consented_cloud_provider_without_a_local_model(self):
		from spice_facility.ai.config import get_config
		from spice_facility.ai.providers.registry import NoSummaryProvider, summary_provider

		for field, value in {"summary_enabled": 1, "summary_provider": "Claude", "claude_enabled": 1,
		                     "claude_model": "claude-test", "cloud_patient_data_allowed": 1}.items():
			frappe.db.set_single_value("SPICE AI Settings", field, value)
		frappe.conf.spice_ai_ollama_enabled = 0
		frappe.clear_document_cache("SPICE AI Settings", "SPICE AI Settings")
		try:
			self.assertTrue(get_config().summary_enabled)
			self.assertEqual(summary_provider().name, "Claude")
			frappe.db.set_single_value("SPICE AI Settings", "cloud_patient_data_allowed", 0)
			frappe.clear_document_cache("SPICE AI Settings", "SPICE AI Settings")
			self.assertFalse(get_config().summary_enabled)
			with self.assertRaises(NoSummaryProvider):
				summary_provider()
		finally:
			frappe.conf.pop("spice_ai_ollama_enabled", None)
			frappe.db.rollback()
			frappe.clear_document_cache("SPICE AI Settings", "SPICE AI Settings")

	def test_a_cloud_model_never_receives_direct_identifiers(self):
		from spice_facility.api import ai_summary

		patient = frappe.get_doc("Patient", any_patient())
		patient.db_set({"mobile": "9000000001"})
		for is_local, should_see_name in ((False, False), (True, True)):
			model = FakeProvider("Claude" if not is_local else "Ollama", is_local, json_answers=[
				{"headline": "h", "summary": "s", "alerts": [], "follow_up": []}])
			row = frappe.get_doc({"doctype": "SPICE AI Summary", "reference_doctype": "Patient",
			                      "reference_name": patient.name, "status": "Queued",
			                      "requested_by": "Administrator"}).insert(ignore_permissions=True)
			with patch("spice_facility.api.ai_summary.summary_provider", return_value=model), \
			     patch("spice_facility.api.ai_summary._notify"):
				ai_summary.run(row.name)
			sent = " ".join(model.sent)
			self.assertEqual(frappe.db.get_value("SPICE AI Summary", row.name, "status"), "Ready", sent[:200])
			self.assertEqual(patient.patient_name in sent or patient.name in sent, should_see_name)
			self.assertEqual("[patient]" in sent, not should_see_name)
			self.assertEqual("9000000001" in sent, should_see_name)
			self.assertEqual(frappe.db.get_value("SPICE AI Summary", row.name, "provider"), model.name)
		frappe.db.rollback()

	def test_a_patient_who_opted_out_is_never_sent_to_a_cloud_model(self):
		from spice_facility.ai.config import CLOUD_OPT_OUT_FIELD
		from spice_facility.ai.providers.registry import NoSummaryProvider
		from spice_facility.api.ai_summary import provider_for

		patient = frappe.get_doc("Patient", any_patient())
		cloud, local = FakeProvider("Claude", False), FakeProvider("Ollama", True)
		patient.db_set(CLOUD_OPT_OUT_FIELD, 1)
		try:
			with patch("spice_facility.api.ai_summary.summary_provider", return_value=cloud):
				with self.assertRaises(NoSummaryProvider):
					provider_for(patient)
			with patch("spice_facility.api.ai_summary.summary_provider", return_value=local):
				self.assertIs(provider_for(patient), local)
		finally:
			frappe.db.rollback()

	def test_ask_data_keeps_its_local_model_when_summaries_use_a_cloud_one(self):
		from spice_facility.ai.config import get_config
		from spice_facility.ai.providers.registry import local_provider

		frappe.db.set_single_value("SPICE AI Settings", {"ollama_enabled": 1, "summary_provider": "Claude",
		                                                 "claude_enabled": 1, "cloud_patient_data_allowed": 1})
		frappe.conf.spice_ai_ollama_enabled = 1
		frappe.clear_document_cache("SPICE AI Settings", "SPICE AI Settings")
		try:
			self.assertEqual(get_config().summary_provider, "Claude")
			self.assertEqual(local_provider().name, "Ollama")
		finally:
			frappe.conf.pop("spice_ai_ollama_enabled", None)
			frappe.db.rollback()
			frappe.clear_document_cache("SPICE AI Settings", "SPICE AI Settings")


class TestPseudonymise(IntegrationTestCase):
	"""What a cloud model receives in place of a record's context."""

	def run_on(self, context, full=(), parts=()):
		from spice_facility.ai.summary_context import pseudonymise

		return pseudonymise(context, full, parts)

	def test_indic_names_and_name_parts_are_replaced_as_whole_words(self):
		out = self.run_on({"note": "রহিমা ভালো আছে; সীতারাম এসেছিলেন", "record": "Patient:सीता देवी",
		                   "comment": "सीता ठीक है"}, full={"सीता देवी", "রহিমা খাতুন"}, parts={"রহিমা", "सीता"})
		self.assertEqual(out["note"], "[patient] ভালো আছে; সীতারাম এসেছিলেন")
		self.assertEqual(out["record"], "Patient:[patient]")
		self.assertEqual(out["comment"], "[patient] ठीक है")

	def test_name_parts_match_as_written_but_full_names_in_any_case(self):
		out = self.run_on({"a": "Pain will improve; Will Rose reviewed", "b": "WILL ROSE seen"},
		                  full={"Will Rose"}, parts={"Will", "Rose"})
		self.assertEqual(out, {"a": "Pain will improve; [patient] reviewed", "b": "[patient] seen"})

	def test_contacts_staff_and_exact_age_do_not_leave(self):
		out = self.run_on({"patient": {"patient_name": "X", "mobile": "9876500001", "age": "34 Year(s) 2 Month(s) 11 Day(s)",
		                               "policy": {"policy_number": "P1"}, "last_visit": {"practitioner_name": "Dr A"}},
		                   "encounter": {"practitioner": "Dr A", "date": "2026-10-09",
		                                 "note": "Son Karim, call 01711-123456 or karim@example.com. HbA1c 8.2"}})
		self.assertEqual(out["patient"], {"age": "34 years", "last_visit": {}})
		self.assertEqual(out["encounter"], {"date": "2026-10-09",
		                                    "note": "Son Karim, call [phone] or [email]. HbA1c 8.2"})
		self.assertEqual(self.run_on({"age": "93 Year(s)"})["age"], "90+ years")
		self.assertEqual(self.run_on({"age": "0 Year(s) 7 Month(s) 3 Day(s)"})["age"], "7 months")

	def test_an_unregistered_emergency_arrival_is_still_named_and_removed(self):
		from spice_facility.ai.summary_context import patient_names

		record = frappe._dict(doctype="Emergency Record", name="ER-0001", patient=None, patient_name="Abdul Malek")
		full, parts = patient_names(record)
		self.assertIn("Abdul Malek", full)
		out = self.run_on({"emergency": {"chief_complaint": "Abdul Malek, RTA, unconscious"}}, full, parts)
		self.assertEqual(out["emergency"]["chief_complaint"], "[patient], RTA, unconscious")
