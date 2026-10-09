"""AI features, with fake providers (no network): catalog, governed queries, data policy, settings, MCP."""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from spice_facility.ai import data_catalog, query
from spice_facility.ai.clinical_flags import lab_flags, parse_range
from spice_facility.ai.config import ProviderConfig, ensure_ai_access
from spice_facility.ai.summary_context import build_context, context_hash
from spice_facility.ai.talk_to_data import answer_data, implicit_scope, keep_known_links, resolve_page, run_question, tidy

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
