"""Talk-to-Data endpoints: ask (queued), fetch an answer, list providers, pin a chart, export CSV."""

import csv
import io
import json

import frappe
from frappe import _

from spice_facility.ai import data_catalog, query
from spice_facility.ai.config import ensure_ai_access, get_config
from spice_facility.ai.providers.base import AIProviderError
from spice_facility.ai.talk_to_data import run_question
from spice_facility.api.ai_summary import enforce_rate_limit

LOG = "SPICE AI Query Log"
EVENT = "spice_ai_answer"
MAX_QUESTION = 500
BUCKET_INTERVAL = {"day": "Daily", "week": "Weekly", "month": "Monthly", "year": "Yearly"}
CHART_TYPE = {"bar": "Bar", "line": "Line", "pie": "Pie", "donut": "Donut"}


def _own_log(name):
	log = frappe.get_doc(LOG, name)
	if log.user != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return log


@frappe.whitelist()
def settings() -> dict:
	"""What the chat UI needs to render: enabled providers, default, feature switches. No secrets."""
	config = get_config()
	allowed = _user_allowed()
	available = config.chat_providers()
	usable = bool(config.chat_enabled and allowed and available)
	return {
		"allowed": allowed, "chat_enabled": usable, "floating_chat_enabled": usable and config.floating_chat_enabled,
		"default_provider": config.chat_default_provider if config.chat_default_provider in available else next(iter(available), None),
		"local_model": config.has_local(),
		"providers": [{"name": p.name, "model": p.model, "is_local": p.is_local}
		              for p in config.providers.values() if p.enabled and p.in_chat],
	}


def _user_allowed():
	from spice_facility.ai.config import user_can_use_ai

	return user_can_use_ai()


@frappe.whitelist(methods=["POST"])
def ask(question: str, provider: str | None = None, page: str | None = None, use_context: int = 1,
        history: str | None = None) -> dict:
	ensure_ai_access()
	if not get_config().chat_enabled:
		frappe.throw(_("Ask Data is turned off."), frappe.ValidationError)
	question = (question or "").strip()[:MAX_QUESTION]
	if not question:
		frappe.throw(_("Please type a question."), frappe.ValidationError)
	enforce_rate_limit()
	page = json.loads(page) if isinstance(page, str) and page else (page or None)
	log = frappe.get_doc({"doctype": LOG, "user": frappe.session.user, "question": question, "status": "Queued",
	                      "provider": provider or get_config().chat_default_provider,
	                      "context_doctype": (page or {}).get("doctype") if use_context else None,
	                      "context_name": (page or {}).get("name") if use_context else None}).insert(ignore_permissions=True)
	frappe.enqueue("spice_facility.api.talk_to_data.run", queue="long", timeout=600, log=log.name, provider=provider,
	               page=page, use_context=int(use_context), history=history, job_id=f"spice-ai-ask::{log.name}",
	               enqueue_after_commit=True)
	return {"log": log.name, "status": "Queued"}


def run(log, provider=None, page=None, use_context=1, history=None):
	row = frappe.get_doc(LOG, log)
	row.db_set("status", "Running")
	try:
		frappe.set_user(row.user)
		answer, meta = run_question(row.question, provider, page, bool(use_context),
		                            json.loads(history) if isinstance(history, str) and history else history)
		row.db_set({"status": "Answered", "answer_json": json.dumps(answer, default=str),
		            "spec": json.dumps(answer.get("spec")) if answer.get("spec") else None, **meta})
	except (AIProviderError, query.QueryValidationError, frappe.ValidationError, frappe.PermissionError) as error:
		row.db_set({"status": "Failed", "error": str(error)[:500]})
	except Exception as error:  # noqa: BLE001 — keep the chat responsive; full trace goes to the Error Log
		row.db_set({"status": "Failed", "error": _("Unexpected error; see Error Log.")})
		frappe.log_error(title="SPICE Ask Data failed", message=frappe.get_traceback())
	finally:
		frappe.set_user("Administrator")
	frappe.publish_realtime(EVENT, {"log": log, "status": frappe.db.get_value(LOG, log, "status")},
	                        user=row.user, after_commit=True)


@frappe.whitelist()
def get_answer(log: str) -> dict:
	row = _own_log(log)
	return {"log": row.name, "status": row.status, "question": row.question, "error": row.error,
	        "answer": json.loads(row.answer_json) if row.answer_json else None}


@frappe.whitelist()
def providers() -> list:
	return settings()["providers"]


def chart_args(spec, dataset, title):
	"""Dashboard Chart arguments for a spec, or None when the spec has no Dashboard Chart equivalent."""
	agg, field = spec["measure"]["agg"], spec["measure"]["field"]
	filters = json.dumps([[dataset["doctype"], f["field"], f["op"], f["value"]] for f in spec["filters"]
	                      if f["op"] in ("=", "!=", ">", ">=", "<", "<=", "like")])
	chart_type = CHART_TYPE.get(spec["output"], "Bar")
	if spec["time_bucket"] != "none" and not spec["group_by"] and agg in ("count", "sum", "avg"):
		return {"chart_name": title, "chart_type": {"count": "Count", "sum": "Sum", "avg": "Average"}[agg],
		        "type": chart_type if chart_type in ("Bar", "Line") else "Line", "document_type": dataset["doctype"],
		        "based_on": dataset["date_field"], "value_based_on": field or None, "timeseries": 1,
		        "time_interval": BUCKET_INTERVAL[spec["time_bucket"]], "timespan": "Last Year",
		        "filters_json": filters, "is_public": 1}
	if spec["group_by"] and spec["time_bucket"] == "none" and agg in ("count", "sum", "avg"):
		return {"chart_name": title, "chart_type": "Group By", "type": chart_type, "document_type": dataset["doctype"],
		        "group_by_based_on": spec["group_by"], "group_by_type": {"count": "Count", "sum": "Sum", "avg": "Average"}[agg],
		        "aggregate_function_based_on": field or None, "filters_json": filters, "is_public": 1}
	return None


@frappe.whitelist(methods=["POST"])
def pin_chart(log: str, title: str) -> dict:
	"""Save the answer's chart as a Dashboard Chart, through frappe_theme's validated creator."""
	from frappe_theme.controllers.mcp_tools import create_dashboard_chart

	ensure_ai_access()
	frappe.has_permission("Dashboard Chart", "create", throw=True)
	row = _own_log(log)
	answer = json.loads(row.answer_json or "{}")
	dataset = data_catalog.get(answer.get("dataset"))
	args = chart_args(answer.get("spec") or {}, dataset, (title or answer.get("title") or "")[:140]) if dataset else None
	if not args:
		frappe.throw(_("This answer cannot be saved as a Dashboard Chart."), frappe.ValidationError)
	if frappe.db.exists("Dashboard Chart", args["chart_name"]):
		frappe.throw(_("A Dashboard Chart named {0} already exists.").format(args["chart_name"]), frappe.DuplicateEntryError)
	return create_dashboard_chart(**{k: v for k, v in args.items() if v is not None})


@frappe.whitelist()
def export_csv(log: str):
	row = _own_log(log)
	table = (json.loads(row.answer_json or "{}") or {}).get("table")
	if not table:
		frappe.throw(_("This answer has no table to export."), frappe.ValidationError)
	buffer = io.StringIO()
	writer = csv.writer(buffer)
	writer.writerow([c["label"] for c in table["columns"]])
	for r in table["rows"]:
		writer.writerow([r.get("display", r.get(c["field"])) if c["field"] == "label" else r.get(c["field"])
		                 for c in table["columns"]])
	frappe.response.update({"type": "download", "filename": f"ask-data-{row.name}.csv", "filecontent": buffer.getvalue()})
