"""Talk-to-Data orchestration.

Data policy (decided with the user): the chosen chat provider — possibly a cloud model — sees only the
question, the dataset/field names and earlier query plans. Query results are only ever given to the local
model, which writes every narrative. Questions about the open record are answered by the local model; when there
is none, by the chosen cloud model only if the site consented to cloud models reading patient records (the same
consent as AI summaries), the patient has not opted out, and with the record pseudonymised first.

    ask -> mode (record | data)
      record: the record reader (record_reader) answers from the record's grounded context
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
from spice_facility.ai.config import LOCAL_PROVIDERS, get_config
from spice_facility.ai.providers.registry import chat_provider, has_local_provider, local_provider
from spice_facility.ai.summary_context import IDENTIFYING_KEYS
from spice_facility.ai.summary_context import SUPPORTED as RECORD_DOCTYPES
from spice_facility.ai.summary_context import (
	build_context,
	kept_out_of_cloud,
	patient_names,
	pseudonymise,
	record_refs,
	restore_placeholder,
	serialise,
)

LINK_TOKEN = re.compile(r"\[\[([^:\]]+):([^\]]+)\]\]")
# Words that ask for a period or a time series; without them a small model's date range / bucket is dropped.
PERIOD_WORDS = re.compile(r"\b(today|yesterday|week|month|quarter|year|daily|weekly|monthly|yearly|annual|since|"
                          r"between|last|this|past|recent|20\d\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)
SERIES_WORDS = re.compile(r"\b(per|by|each|every|over)\s+(day|week|month|quarter|year)|\b(daily|weekly|monthly|"
                          r"yearly|trend|over time|time series)\b", re.I)
MODE_SCHEMA = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["record", "data"]}},
               "required": ["mode"]}
NARRATE_ROWS = 50
# Filter values on these fields identify a person; they never go to a cloud planner inside an earlier plan.
IDENTIFYING_FIELDS = IDENTIFYING_KEYS | {"patient", "mobile_no", "phone_no", "email_id", "customer", "customer_name",
                                         "inpatient_record", "insurance_subscriber", "ref_practitioner",
                                         "primary_practitioner"}
REDACTED = "[redacted]"
MAX_CITED = 20
HISTORY_TURNS = 2


# ─── page context ───────────────────────────────────────────────────────────


def record_label(doctype, name):
	"""A record's title for display, or its name when the title field is missing or field-level restricted."""
	meta = frappe.get_meta(doctype)
	title_field = meta.get_title_field()
	field = meta.get_field(title_field) if title_field and title_field != "name" else None
	if not field or field.permlevel:
		return name
	return frappe.db.get_value(doctype, name, title_field) or name


def resolve_page(page):
	"""Validate the page the question came from; returns {doctype, name, label} or None."""
	if not page or not page.get("doctype"):
		return None
	doctype, name = page.get("doctype"), page.get("name")
	if name:
		if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
			return None
		return {"doctype": doctype, "name": name, "label": record_label(doctype, name), "kind": "form"}
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
		return {dataset["scopes"]["Patient"]: patient}, [_("Patient = {0} (this page)").format(record_label("Patient", patient))]
	if dataset["doctype"] == doctype:
		# the open record's own kind of data with nothing above to tie it (e.g. Patient asked from a patient):
		# that one record. A record that belongs to a patient was scoped to the patient just above.
		return {"name": name}, [_("{0} = {1} (this page)").format(_(doctype), page["label"])]
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


def cited_records(text, page):
	"""The records an answer rests on, for its Sources panel: the open record, then each one the text links to.

	Only records the user may read are listed; the text already names them, so nothing new is revealed."""
	refs = ([(page["doctype"], page["name"])] if page and page.get("name") else []) + [
		(m.group(1).strip(), m.group(2).strip()) for m in LINK_TOKEN.finditer(text or "")]
	sources, seen = [], set()
	for doctype, name in refs[:MAX_CITED]:
		if (doctype, name) in seen or not frappe.db.exists("DocType", doctype) or not frappe.db.exists(doctype, name) \
				or not frappe.has_permission(doctype, "read", doc=name):
			continue
		seen.add((doctype, name))
		sources.append({"doctype": doctype, "name": name, "label": record_label(doctype, name)})
	return sources


def keep_known_links(text, allowed):
	"""Keep [[Doctype:name]] only for records the answer is grounded on; otherwise show plain text."""
	def replace(match):
		doctype, name = match.group(1).strip(), match.group(2).strip()
		return f"[[{doctype}:{name}]]" if f"{doctype}:{name}" in allowed else name
	return LINK_TOKEN.sub(replace, text or "")


# ─── pipeline ───────────────────────────────────────────────────────────────


# Why no model may read the open record (shown on the answer card)
NO_RECORD_MODEL, OTHER_MODEL, OPTED_OUT = "no_model", "other_model", "opted_out"


def record_reader(page, provider):
	"""(model, None) for the model that may read the open record to answer about it, or (None, reason).

	The local model; otherwise the cloud model the site's consent names (SPICE AI Settings: cloud patient data allowed,
	with the agreement for its summary provider) when that is the model chosen, and never for a patient who opted
	out of cloud AI."""
	if not page or page["kind"] != "form" or page["doctype"] not in RECORD_DOCTYPES:
		return None, None
	if has_local_provider():
		return local_provider(), None
	config = get_config()
	if not config.cloud_patient_data:
		return None, NO_RECORD_MODEL
	if provider.is_local or provider.name != config.summary_provider:
		return None, OTHER_MODEL  # the agreement covers the summary provider, not any cloud model
	if kept_out_of_cloud(frappe.get_doc(page["doctype"], page["name"])):
		return None, OPTED_OUT
	return provider, None


def choose_mode(question, page, use_context, provider, reader):
	if not use_context or not reader:
		# no model may read this record: the question is answered from the data
		return "data"
	if not reader.is_local:
		question = _cloud_question(question, page)
	system = (f"The user is looking at one {page['doctype']} record. Decide if the question is about THAT record "
	          "(its details, history, results, summary) -> record, or asks for counts, trends or lists across "
	          "many records (even if limited to this record's related data) -> data.")
	return provider.complete_json(system, [{"role": "user", "content": question}], MODE_SCHEMA).get("mode", "data")


def _cloud_question(question, page):
	"""The question as a cloud model reading this record may see it: the patient's names and contacts replaced like
	the record's, or a name in the question would undo the record's pseudonymisation."""
	doc = frappe.get_doc(page["doctype"], page["name"])
	return pseudonymise({"question": question}, *patient_names(doc))["question"]


def answer_record(question, page, reader):
	doc = frappe.get_doc(page["doctype"], page["name"])
	context = build_context(doc)
	# a cloud reader gets the record, and the question, pseudonymised, as cloud summaries are
	if not reader.is_local:
		context_sent, question = pseudonymise(context, *patient_names(doc)), _cloud_question(question, page)
	else:
		context_sent = context
	text = reader.complete_text(prompts.RECORD_QA_SYSTEM, [{"role": "user", "content":
	                            f"Context:\n{serialise(context_sent, get_config().max_context_chars)}\n\nQuestion: {question}"}])
	if not reader.is_local:
		text = restore_placeholder(text, _("the patient"))
	text = keep_known_links(text, record_refs(context) | {f"{doc.doctype}:{doc.name}"})
	return {"mode": "record", "text": text, "narrator": reader.model, "narrator_is_local": reader.is_local,
	        "sources": cited_records(text, page)}


def redact_plan(spec):
	"""An earlier plan as a cloud planner may see it: identifying filter values replaced by a placeholder, and no
	title (a model-written title can echo a name)."""
	spec = {**(spec or {}), "title": ""}
	spec["filters"] = [{**f, "value": REDACTED} if f.get("field") in IDENTIFYING_FIELDS and f.get("value") else f
	                   for f in spec.get("filters") or [] if isinstance(f, dict)]
	return spec


def plan(question, provider, history, page, datasets):
	"""Chat provider sees: question, dataset/field names, earlier plans. Nothing else.

	`history` comes from the user's own logs of the same chat (never from the browser), and holds plans only: page
	and list-view scope is kept out of stored plans, and a cloud planner gets identifying filter values redacted."""
	turns = [h for h in (history or [])[-HISTORY_TURNS:] if h.get("spec")]
	if not provider.is_local:
		# A question first put to a local model may name a patient: it is never forwarded to a cloud planner.
		turns = [h for h in turns if h.get("provider") and h["provider"] not in LOCAL_PROVIDERS]
	shown_plan = (lambda spec: spec) if provider.is_local else redact_plan
	prior = "\n".join(f"Earlier question: {h.get('question')}\nEarlier plan: {json.dumps(shown_plan(h.get('spec')))}"
	                  for h in turns)
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
	if page and page["kind"] == "form" and (dataset["doctype"] == page["doctype"] or dataset["scopes"].get(page["doctype"])
	                                        or "Patient" in dataset["scopes"]):
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
	# List-view filters scope this answer only. They stay out of the stored plan, which later goes back to the
	# planner as history, and travel beside it as `scope_filters` (e.g. for pinning the chart).
	scope_filters = []
	if use_context:
		scope_filters, labels = list_scope(dataset, page)
		scope_labels += labels
	result = query.execute({**spec, "filters": spec["filters"] + scope_filters}, dataset, scope, config.max_rows)
	shown = query.present(result, spec, dataset)
	source = query.describe_parts(spec, dataset, scope_labels)

	preview = shown.get("table", {}).get("rows", [])[:NARRATE_ROWS] if "table" in shown else []
	if not has_local_provider():
		return {"mode": "data", "text": plain_summary(shown, spec, dataset, result["rows_read"]),
		        "title": spec["title"] or dataset["label"], "dataset": dataset["doctype"], "spec": spec,
		        "how": query.describe(spec, dataset, scope_labels), "source": source, "scope": scope_labels,
		        "rows_read": result["rows_read"], "truncated": result["truncated"], "narrator": None,
		        "scope_filters": scope_filters, **shown}
	local = local_provider()
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
		"dataset": dataset["doctype"], "spec": spec, "how": query.describe(spec, dataset, scope_labels), "source": source,
		"scope": scope_labels, "rows_read": result["rows_read"], "truncated": result["truncated"],
		"narrator": local.model, "scope_filters": scope_filters, **shown,
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


def plain_summary(shown, spec, dataset, rows_read):
	"""A written answer built from the result itself, used when no local model may narrate it."""
	if shown.get("number"):
		return _("{0}: {1}").format(shown["number"]["label"], frappe.format(shown["number"]["value"], {"fieldtype": "Float"}))
	rows = (shown.get("table") or {}).get("rows") or []
	if not rows:
		return _("No matching records.")
	if shown["kind"] == "groups":
		top = ", ".join(f"{r.get('display', r.get('label'))} ({frappe.format(r['value'], {'fieldtype': 'Float'})})" for r in rows[:5])
		return _("{0} by {1}: {2}.").format(rows and shown["table"]["columns"][1]["label"],
		                                     shown["table"]["columns"][0]["label"], top)
	if shown["kind"] == "series":
		return _("{0} across {1} periods; see the chart and table.").format(dataset["label"], len(rows))
	return _("{0} matching {1}; showing {2}.").format(rows_read, dataset["label"].lower(), len(rows))


def run_question(question, provider_name=None, page=None, use_context=True, history=None):
	"""Answer one question. Returns (answer, meta) where meta feeds the query log."""
	provider = chat_provider(provider_name)
	page = resolve_page(page) if use_context else None
	started = time.monotonic()
	reader, no_reader_reason = record_reader(page, provider) if use_context else (None, None)
	mode = choose_mode(question, page, use_context, provider, reader)
	answer = answer_record(question, page, reader) if mode == "record" else answer_data(question, provider, history, page, use_context)
	answer.update({"provider": provider.name, "planner": provider.model, "planner_is_local": provider.is_local,
	               "page": page,
	               # asked from a record that has record answers, but no model may read it: why, for the card
	               "record_fallback": no_reader_reason if mode == "data" else None})
	return answer, {"mode": mode.title(), "duration_ms": round((time.monotonic() - started) * 1000),
	                "row_count": answer.get("rows_read") or 0, "provider": provider.name, "model": provider.model}
