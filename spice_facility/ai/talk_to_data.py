"""Talk-to-Data orchestration.

Data policy (decided with the user): the chosen chat provider — possibly a cloud model — sees only the
question, the dataset/field names and earlier query plans. Records, query results and page/record context
are only ever given to the local model, which writes every narrative and answers questions about the open
record.

    ask -> mode (record | data)
      record: local model answers from the record's grounded context
      data:   chat provider plans (dataset, then QuerySpec) -> validate -> execute as the user with the
              page scope -> table / chart / drill-downs -> local model narrates
"""

import json
import re
import time

import frappe
from frappe import _
from frappe.utils import nowdate

from spice_facility.ai import data_catalog, prompts, query
from spice_facility.ai.config import get_config
from spice_facility.ai.providers.registry import chat_provider, local_provider
from spice_facility.ai.summary_context import SUPPORTED as RECORD_DOCTYPES
from spice_facility.ai.summary_context import build_context, record_refs, serialise

LINK_TOKEN = re.compile(r"\[\[([^:\]]+):([^\]]+)\]\]")
# Words that ask for a period or a time series; without them a small model's date range / bucket is dropped.
PERIOD_WORDS = re.compile(r"\b(today|yesterday|week|month|quarter|year|daily|weekly|monthly|yearly|annual|since|"
                          r"between|last|this|past|recent|20\d\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)
SERIES_WORDS = re.compile(r"\b(per|by|each|every|over)\s+(day|week|month|quarter|year)|\b(daily|weekly|monthly|"
                          r"yearly|trend|over time|time series)\b", re.I)
MODE_SCHEMA = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["record", "data"]}},
               "required": ["mode"]}
NARRATE_ROWS = 50
HISTORY_TURNS = 2


# ─── page context ───────────────────────────────────────────────────────────


def resolve_page(page):
	"""Validate the page the question came from; returns {doctype, name, label} or None."""
	if not page or not page.get("doctype"):
		return None
	doctype, name = page.get("doctype"), page.get("name")
	if name:
		if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
			return None
		title_field = frappe.get_meta(doctype).get_title_field()
		label = frappe.db.get_value(doctype, name, title_field) if title_field else name
		return {"doctype": doctype, "name": name, "label": label or name, "kind": "form"}
	if not frappe.has_permission(doctype, "read"):
		return None
	filters = page.get("filters") if isinstance(page.get("filters"), list) else []
	return {"doctype": doctype, "filters": filters[:6], "label": doctype, "kind": "list"}


def implicit_scope(dataset, page):
	"""Fields that tie a dataset to the open record, e.g. Lab Test.patient = <open patient>."""
	if not page or page["kind"] != "form":
		return {}, []
	doctype, name = page["doctype"], page["name"]
	field = dataset["scopes"].get(doctype)
	if field:
		return {field: name}, [_("{0} = {1} (this page)").format(dataset["fields"].get(field, {}).get("label", field), page["label"])]
	# a record that belongs to a patient scopes patient-level datasets to that patient
	patient = frappe.db.get_value(doctype, name, "patient") if frappe.get_meta(doctype).has_field("patient") else None
	if patient and "Patient" in dataset["scopes"]:
		patient_name = frappe.db.get_value("Patient", patient, "patient_name")
		return {dataset["scopes"]["Patient"]: patient}, [_("Patient = {0} (this page)").format(patient_name or patient)]
	return {}, []


def list_scope(dataset, page):
	"""Filters of the list view the question came from, when it lists this dataset."""
	if not page or page["kind"] != "list" or page["doctype"] != dataset["doctype"]:
		return [], []
	extra = []
	for f in page["filters"]:
		if isinstance(f, list) and len(f) >= 4 and f[1] in dataset["fields"] and f[2] in query.OPS:
			extra.append({"field": f[1], "op": f[2], "value": ",".join(map(str, f[3])) if isinstance(f[3], list) else str(f[3])})
	return extra, ([_("Filters from this list")] if extra else [])


# ─── links ──────────────────────────────────────────────────────────────────


def keep_known_links(text, allowed):
	"""Keep [[Doctype:name]] only for records the answer is grounded on; otherwise show plain text."""
	def replace(match):
		doctype, name = match.group(1).strip(), match.group(2).strip()
		return f"[[{doctype}:{name}]]" if f"{doctype}:{name}" in allowed else name
	return LINK_TOKEN.sub(replace, text or "")


# ─── pipeline ───────────────────────────────────────────────────────────────


def choose_mode(question, page, use_context, provider):
	if not use_context or not page or page["kind"] != "form" or page["doctype"] not in RECORD_DOCTYPES:
		return "data"
	system = (f"The user is looking at one {page['doctype']} record. Decide if the question is about THAT record "
	          "(its details, history, results, summary) -> record, or asks for counts, trends or lists across "
	          "many records (even if limited to this record's related data) -> data.")
	return provider.complete_json(system, [{"role": "user", "content": question}], MODE_SCHEMA).get("mode", "data")


def answer_record(question, page):
	doc = frappe.get_doc(page["doctype"], page["name"])
	context = build_context(doc)
	local = local_provider()
	text = local.complete_text(prompts.RECORD_QA_SYSTEM, [{"role": "user", "content":
	                           f"Context:\n{serialise(context, get_config().max_context_chars)}\n\nQuestion: {question}"}])
	return {"mode": "record", "text": keep_known_links(text, record_refs(context) | {f"{doc.doctype}:{doc.name}"}),
	        "narrator": local.model}


def plan(question, provider, history, page, datasets):
	"""Chat provider sees: question, dataset/field names, earlier plans. Nothing else."""
	prior = "\n".join(f"Earlier question: {h.get('question')}\nEarlier plan: {json.dumps(h.get('spec'))}"
	                  for h in (history or [])[-HISTORY_TURNS:] if h.get("spec"))
	content = f"{prior}\n\nQuestion: {question}".strip()
	names = [d["doctype"] for d in datasets]
	pick = provider.complete_json(prompts.PICK_DATASET_SYSTEM.format(catalog=data_catalog.catalog_text(datasets)),
	                              [{"role": "user", "content": content}],
	                              {"type": "object", "properties": {"dataset": {"type": "string", "enum": names}},
	                               "required": ["dataset"]})
	dataset = next((d for d in datasets if d["doctype"] == pick.get("dataset")), None)
	if not dataset:
		raise query.QueryValidationError(_("I could not tell which data this question is about."))
	scope_hint = ""
	if page and page["kind"] == "form" and (dataset["scopes"].get(page["doctype"]) or "Patient" in dataset["scopes"]):
		scope_hint = (f"The question is asked from one {page['doctype']} record; the system already limits results to "
		              "that record, so do NOT add filters for it.")
	spec = provider.complete_json(
		prompts.PLAN_QUERY_SYSTEM.format(dataset=dataset["doctype"], fields=data_catalog.fields_text(dataset),
		                                 today=nowdate(), scope=scope_hint),
		[{"role": "user", "content": content}], query.spec_schema(dataset))
	return dataset, spec


def answer_data(question, provider, history, page, use_context):
	config = get_config()
	datasets = data_catalog.available()
	dataset, raw_spec = plan(question, provider, history, page if use_context else None, datasets)
	spec = tidy(query.validate(raw_spec, dataset, config.max_groups), dataset, question)
	scope, scope_labels = implicit_scope(dataset, page) if use_context else ({}, [])
	if use_context:
		extra, labels = list_scope(dataset, page)
		spec["filters"] += extra
		scope_labels += labels
	result = query.execute(spec, dataset, scope, config.max_rows)
	shown = query.present(result, spec, dataset)

	local = local_provider()
	preview = shown.get("table", {}).get("rows", [])[:NARRATE_ROWS] if "table" in shown else []
	measure_type = dataset["fields"].get(spec["measure"]["field"], {}).get("type") or frappe.get_meta(
		dataset["doctype"]).get_field(spec["measure"]["field"]).fieldtype if spec["measure"]["field"] else None
	payload = {"question": question, "plan": query.describe(spec, dataset, scope_labels),
	           "number": shown.get("number"), "rows": preview, "rows_read": result["rows_read"],
	           "currency": frappe.defaults.get_global_default("currency") if measure_type == "Currency" else None}
	text = local.complete_text(prompts.NARRATE_SYSTEM, [{"role": "user", "content": json.dumps(payload, default=str)}],
	                           max_tokens=220)
	allowed = {f"{dataset['doctype']}:{r['name']}" for r in preview if isinstance(r, dict) and r.get("name")}
	return {
		"mode": "data", "text": keep_known_links(text, allowed), "title": spec["title"] or dataset["label"],
		"dataset": dataset["doctype"], "spec": spec, "how": query.describe(spec, dataset, scope_labels),
		"scope": scope_labels, "rows_read": result["rows_read"], "truncated": result["truncated"],
		"narrator": local.model, **shown,
	}


def tidy(spec, dataset, question):
	"""Undo what a small planner tends to over-specify, using what the question actually says."""
	date_fields = {f for f, meta in dataset["fields"].items() if meta["type"] in data_catalog.DATE_TYPES}
	if not PERIOD_WORDS.search(question):
		spec["date_range"] = {"preset": "none", "from": None, "to": None}
	if spec["time_bucket"] != "none" and not SERIES_WORDS.search(question):
		spec["time_bucket"] = "none"
	if spec["group_by"] in date_fields:
		# grouping by a raw date: use a time bucket instead (asked-for bucket, else month)
		spec["time_bucket"] = spec["time_bucket"] if spec["time_bucket"] != "none" else "month"
		spec["group_by"] = ""
	spec["filters"] = [f for f in spec["filters"]
	                   if not (f["field"] == spec["group_by"] and f["op"] in ("is set", "is not set"))
	                   and (f["op"] in ("is set", "is not set") or f["value"].strip())]
	if spec["output"] in ("pie", "donut") and spec["time_bucket"] != "none":
		spec["output"] = "line"
	if spec["output"] == "number" and (spec["group_by"] or spec["time_bucket"] != "none"):
		spec["output"] = "bar" if spec["group_by"] else "line"
	if spec["output"] in ("bar", "line", "pie", "donut", "table") and not spec["group_by"] and spec["time_bucket"] == "none":
		spec["output"] = "number"
	return spec


def run_question(question, provider_name=None, page=None, use_context=True, history=None):
	"""Answer one question. Returns (answer, meta) where meta feeds the query log."""
	provider = chat_provider(provider_name)
	page = resolve_page(page) if use_context else None
	started = time.monotonic()
	mode = choose_mode(question, page, use_context, provider)
	answer = answer_record(question, page) if mode == "record" else answer_data(question, provider, history, page, use_context)
	answer.update({"provider": provider.name, "planner": provider.model, "planner_is_local": provider.is_local,
	               "page": page})
	return answer, {"mode": mode.title(), "duration_ms": round((time.monotonic() - started) * 1000),
	                "row_count": answer.get("rows_read") or 0, "provider": provider.name, "model": provider.model}
