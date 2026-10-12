"""Talk-to-Data endpoints: ask (queued), fetch an answer, list providers, pin a chart, export CSV."""

import csv
import io
import json
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime
from frappe.utils.background_jobs import get_job
from rq.exceptions import InvalidJobOperation, NoSuchJobError

from spice_facility.ai import data_catalog, query
from spice_facility.ai.config import ensure_ai_access, get_config
from spice_facility.ai.providers.base import AIProviderError
from spice_facility.ai.providers.registry import chat_provider
from spice_facility.ai.talk_to_data import HISTORY_TURNS, LINK_TOKEN, run_question
from spice_facility.api.ai_summary import enforce_rate_limit

LOG = "SPICE AI Query Log"
EVENT = "spice_ai_answer"
MAX_QUESTION = 500
BUCKET_INTERVAL = {"day": "Daily", "week": "Weekly", "month": "Monthly", "year": "Yearly"}
CHART_TYPE = {"bar": "Bar", "line": "Line", "pie": "Pie", "donut": "Donut"}
IN_FLIGHT = ("Queued", "Running")
THREAD_ID = re.compile(r"^[A-Za-z0-9]{10,32}$")
MAX_THREADS = 50
MAX_THREAD_TURNS = 50
THREAD_SCAN = 500  # recent questions scanned to build the chat list
STOPPED = "Stopped"


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
        thread: str | None = None) -> dict:
	ensure_ai_access()
	if not get_config().chat_enabled:
		frappe.throw(_("Ask Data is turned off."), frappe.ValidationError)
	question = (question or "").strip()[:MAX_QUESTION]
	if not question:
		frappe.throw(_("Please type a question."), frappe.ValidationError)
	enforce_rate_limit()
	chosen = chat_provider(provider)  # validated now, and logged with the question even if it is stopped or fails
	page = json.loads(page) if isinstance(page, str) and page else (page or None)
	thread = _own_thread(thread) or frappe.generate_hash(length=16)
	log = frappe.get_doc({"doctype": LOG, "user": frappe.session.user, "thread": thread, "question": question, "status": "Queued",
	                      "provider": chosen.name, "model": chosen.model,
	                      "context_doctype": (page or {}).get("doctype") if use_context else None,
	                      "context_name": (page or {}).get("name") if use_context else None}).insert(ignore_permissions=True)
	frappe.enqueue("spice_facility.api.talk_to_data.run", queue="long", timeout=600, log=log.name, provider=chosen.name,
	               page=page, use_context=int(use_context), job_id=_job_id(log.name),
	               enqueue_after_commit=True)
	return {"log": log.name, "status": "Queued", "thread": thread}


def _own_thread(thread):
	"""The thread id to continue, only if it is one of the session user's own chats (ids are minted by the server).

	A chat from before threads existed is keyed by its one question's log name: that question joins the thread."""
	if not thread or not THREAD_ID.match(thread):
		return None
	mine = {"user": frappe.session.user, "hidden": 0}  # a deleted chat is not continued
	if frappe.db.exists(LOG, {**mine, "thread": thread}):
		return thread
	if frappe.db.exists(LOG, {**mine, "name": thread, "thread": ["is", "not set"]}):
		frappe.db.set_value(LOG, thread, "thread", thread, update_modified=False)
		return thread
	return None


def _history(row):
	"""The plans of this chat's earlier answered questions, from the user's own logs (never from the browser)."""
	if not row.thread:
		return []
	earlier = frappe.get_all(LOG, {"user": row.user, "thread": row.thread, "status": "Answered", "hidden": 0,
	                               "creation": ["<", row.creation], "spec": ["is", "set"]},
	                         ["question", "spec", "provider"], order_by="creation desc", limit=HISTORY_TURNS)
	return [{"question": r.question, "spec": json.loads(r.spec), "provider": r.provider} for r in reversed(earlier)]


def _job_id(log):
	return f"spice-ai-ask::{log}"


def _set_if(log, statuses, values):
	"""Compare-and-set: write `values` only while the log's status is one of `statuses`.

	stop() (web request) and run() (worker) race on the same row; the condition in the UPDATE means neither can
	overwrite the other's outcome (a stopped question never becomes Answered, an answered one never Stopped)."""
	table = frappe.qb.DocType(LOG)
	update = frappe.qb.update(table).set(table.modified, now_datetime())
	for field, value in values.items():
		update = update.set(table[field], value)
	update.where((table.name == log) & table.status.isin(list(statuses))).run()


def run(log, provider=None, page=None, use_context=1, history=None):  # `history` kept for jobs queued before upgrade
	row = frappe.get_doc(LOG, log)
	_set_if(log, ("Queued",), {"status": "Running"})
	# Commit before the model call: no row lock is held while it runs, and stop() sees "Running".
	frappe.db.commit()
	if frappe.db.get_value(LOG, log, "status") != "Running":
		return  # stopped before it started
	try:
		frappe.set_user(row.user)
		answer, meta = run_question(row.question, provider, page, bool(use_context), _history(row))
		_set_if(log, ("Running",), {"status": "Answered", "answer_json": json.dumps(answer, default=str),
		                            "spec": json.dumps(answer.get("spec")) if answer.get("spec") else None, **meta})
	except (AIProviderError, query.QueryValidationError, frappe.ValidationError, frappe.PermissionError) as error:
		_set_if(log, ("Running",), {"status": "Failed", "error": str(error)[:500]})
	except Exception:  # noqa: BLE001 — keep the chat responsive; full trace goes to the Error Log
		frappe.log_error(title="SPICE Ask Data failed", message=frappe.get_traceback())
		_set_if(log, ("Running",), {"status": "Failed", "error": _("Unexpected error; see Error Log.")})
	finally:
		frappe.set_user("Administrator")
	frappe.publish_realtime(EVENT, {"log": log, "status": frappe.db.get_value(LOG, log, "status")},
	                        user=row.user, after_commit=True)


def _turn(row):
	return {"log": row.name, "status": row.status, "question": row.question, "error": row.error,
	        "answer": json.loads(row.answer_json) if row.answer_json else None,
	        "asked_on": row.creation, "duration_ms": row.duration_ms, "provider": row.provider, "model": row.model}


@frappe.whitelist()
def threads() -> list:
	"""The user's own conversations, newest first: title (first question), last activity, number of questions.

	Questions asked before threads existed have no thread and stand alone (keyed by their log name)."""
	ensure_ai_access()
	rows = frappe.get_all(LOG, {"user": frappe.session.user, "hidden": 0}, ["name", "thread", "question", "creation"],
	                      order_by="creation desc", limit=THREAD_SCAN)
	found = {}
	for row in rows:
		key = row.thread or row.name
		entry = found.setdefault(key, {"thread": key, "updated": row.creation, "turns": 0})
		entry["turns"] += 1
		entry["title"] = row.question  # rows run newest to oldest, so the last one seen is the first question
	return list(found.values())[:MAX_THREADS]


@frappe.whitelist()
def thread(thread: str) -> list:
	"""The turns of one of the user's conversations, oldest first."""
	ensure_ai_access()
	fields = ["name", "status", "question", "error", "answer_json", "creation", "duration_ms", "provider", "model"]
	# the newest turns when a chat is longer than the cap (they carry the context for a follow-up), oldest first
	rows = frappe.get_all(LOG, _thread_filters(thread), fields, order_by="creation desc", limit=MAX_THREAD_TURNS)
	return [_turn(r) for r in reversed(rows)]


@frappe.whitelist(methods=["POST"])
def delete_thread(thread: str) -> dict:
	"""Remove one of the user's chats from their list. The questions stay in the query log (audit, rate limits)."""
	ensure_ai_access()
	filters = _thread_filters(thread)
	count = frappe.db.count(LOG, filters)
	frappe.db.set_value(LOG, filters, "hidden", 1, update_modified=False)
	return {"thread": thread, "deleted": count}


def _thread_filters(thread):
	"""The user's own, not deleted, questions of one chat. A question from before threads existed has no thread:
	its chat key is its own log name."""
	if not THREAD_ID.match(thread or ""):
		frappe.throw(_("Not a valid chat."), frappe.ValidationError)
	mine = {"user": frappe.session.user, "hidden": 0}
	if frappe.db.exists(LOG, {**mine, "thread": thread}):
		return {**mine, "thread": thread}
	return {**mine, "name": thread, "thread": ["is", "not set"]}


@frappe.whitelist(methods=["POST"])
def stop(log: str) -> dict:
	"""Stop a question the user no longer wants: drop it from the queue, or discard the answer when it arrives."""
	row = _own_log(log)
	if row.status not in IN_FLIGHT:
		return {"log": row.name, "status": row.status}
	if row.status == "Queued" and (job := get_job(_job_id(row.name))):
		try:
			job.cancel()
		except (InvalidJobOperation, NoSuchJobError):
			pass  # already cancelled by a concurrent stop, or already picked up: run() will see Stopped
	_set_if(row.name, IN_FLIGHT, {"status": STOPPED})
	return {"log": row.name, "status": frappe.db.get_value(LOG, row.name, "status")}


@frappe.whitelist()
def get_answer(log: str) -> dict:
	return _turn(_own_log(log))


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
	spec = answer.get("spec") or {}
	# the list-view filters the answer was scoped by belong in the pinned chart too
	spec = {**spec, "filters": (spec.get("filters") or []) + (answer.get("scope_filters") or [])} if spec else spec
	args = chart_args(spec, dataset, (title or answer.get("title") or "")[:140]) if dataset else None
	if not args:
		frappe.throw(_("This answer cannot be saved as a Dashboard Chart."), frappe.ValidationError)
	if frappe.db.exists("Dashboard Chart", args["chart_name"]):
		frappe.throw(_("A Dashboard Chart named {0} already exists.").format(args["chart_name"]), frappe.DuplicateEntryError)
	return create_dashboard_chart(**{k: v for k, v in args.items() if v is not None})


# ─── PDF of a chat ───────────────────────────────────────────────────────────

PDF_TEMPLATE = "spice_facility/templates/ask_data/thread_pdf.html"
PDF_MAX_ROWS = 100
PDF_OPTIONS = {"page-size": "A4", "margin-top": "14mm", "margin-bottom": "16mm", "margin-left": "12mm",
               "margin-right": "12mm", "encoding": "UTF-8", "footer-font-size": "7", "footer-spacing": "4"}
FILENAME_UNSAFE = re.compile(r"[^\w\s.-]", re.UNICODE)
MAX_FILENAME = 80


def safe_filename(name, fallback="ask-data"):
	"""A download name a user typed, made safe: ASCII (Frappe's download header can't carry other scripts), no path
	parts or quotes, single spaces, capped, ending in .pdf."""
	name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
	name = FILENAME_UNSAFE.sub("", name).replace("..", ".")
	name = re.sub(r"\s+", " ", name).strip(" .")[:MAX_FILENAME].strip(" .") or fallback
	return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def _plain_text(text, labels):
	"""Answer text with record links as plain labels (a PDF has no desk to open them in)."""
	return LINK_TOKEN.sub(lambda m: labels.get(f"{m.group(1).strip()}:{m.group(2).strip()}") or m.group(2).strip(),
	                      text or "")


def _cell(row, column):
	value = row.get("display", row.get("label")) if column["field"] == "label" else row.get(column["field"])
	if value in (None, ""):
		return "—"
	if column.get("type") in ("Float", "Currency", "Int", "Percent"):
		return frappe.format(value, {"fieldtype": "Float" if column["type"] == "Float" else column["type"]})
	if column.get("type") in ("Date", "Datetime"):
		return frappe.format(value, {"fieldtype": column["type"]})
	return str(value)


def _pdf_visual(answer):
	"""What the PDF shows under the text: ranked bars for groups, otherwise the answer's table (capped)."""
	table = answer.get("table") or {}
	rows, columns = table.get("rows") or [], table.get("columns") or []
	if answer.get("kind") == "groups" and rows and len(columns) > 1:
		top = max((flt(r.get("value")) for r in rows), default=0) or 1
		return {"kind": "bars", "rows": [{"label": r.get("display", r.get("label")), "value": _cell(r, columns[1]),
		                                  "width": round(flt(r.get("value")) / top * 100, 1)} for r in rows]}
	if not rows:
		return None
	return {"kind": "table", "columns": [c["label"] for c in columns],
	        "rows": [[_cell(r, c) for c in columns] for r in rows[:PDF_MAX_ROWS]],
	        "more": _("First {0} of {1} rows").format(PDF_MAX_ROWS, len(rows)) if len(rows) > PDF_MAX_ROWS else None}


def _names_a_question(filename, rows):
	name = re.sub(r"(?i)\.pdf$", "", (filename or "").strip()).casefold()
	return bool(name) and any(r.question and name.startswith(r.question.strip().casefold()) for r in rows)


def _pdf_turn(row, include_questions):
	answer = json.loads(row.answer_json or "{}") or {}
	labels = {f"{r['doctype']}:{r['name']}": r.get("label") for r in answer.get("sources") or []}
	number = answer.get("number") or ({"value": answer["total"], "label": (answer.get("source") or {}).get("dataset")}
	                                  if answer.get("total") is not None else None)
	source = query.describe_line(answer.get("source") or {}) or answer.get("how") or ""
	return {
		"question": row.question if include_questions else None,
		"title": answer.get("title") if answer.get("mode") == "data" else None,
		"kpi": {"value": frappe.format(number["value"], {"fieldtype": "Float"}), "label": number.get("label") or ""}
		if number else None,
		"text": _plain_text(answer.get("text"), labels),
		"visual": _pdf_visual(answer),
		"source": source,
		"meta": " · ".join(p for p in [
			_("{0} records read").format(answer["rows_read"]) if answer.get("rows_read") is not None else None,
			" · ".join(m for m in [row.provider, row.model] if m),
			frappe.format(row.creation, {"fieldtype": "Datetime"})] if p),
	}


@frappe.whitelist(methods=["POST"])  # POST: the file name (often a ward or patient label) stays out of URLs and logs
def export_thread_pdf(thread: str, include_questions: int = 1, filename: str | None = None):
	"""One of the user's chats as a PDF of its answers (optionally with the questions), from the stored answers."""
	from frappe.core.doctype.access_log.access_log import make_access_log
	from frappe.utils.pdf import get_pdf

	ensure_ai_access()
	enforce_rate_limit()
	include_questions = cint(include_questions)
	filters = {**_thread_filters(thread), "status": "Answered"}
	rows = frappe.get_all(LOG, filters, ["name", "question", "answer_json", "provider", "model", "creation"],
	                      order_by="creation desc", limit=MAX_THREAD_TURNS)
	if not rows:
		frappe.throw(_("This chat has no answers to download."), frappe.ValidationError)
	rows.reverse()
	turns = [_pdf_turn(r, include_questions) for r in rows]
	if not include_questions and _names_a_question(filename, rows):
		filename = None  # a name that repeats a question would put it back into the heading and the file name
	# The heading is the name the user gave the file; otherwise the chat's first question, or, when questions are
	# left out, the first answer's title (never a question the user chose not to include).
	title = (re.sub(r"(?i)\.pdf$", "", (filename or "").strip())
	         or (rows[0].question if include_questions else next((t["title"] for t in turns if t["title"]), _("Ask Data"))))
	html = frappe.render_template(PDF_TEMPLATE, {
		"title": title, "turns": turns,
		"exported_by": frappe.utils.get_fullname(frappe.session.user), "exported_on": frappe.format(now_datetime(), {"fieldtype": "Datetime"}),
		"include_questions": include_questions,
	})
	# every page carries the caveat and its page number
	pdf = get_pdf(html, {**PDF_OPTIONS, "footer-left": _("AI-generated answers — verify before clinical use."),
	                     "footer-right": _("Page [page] of [topage]")})
	make_access_log(doctype=LOG, document=rows[0].name, method="Ask Data PDF", file_type="PDF",
	                filters=json.dumps({"thread": thread, "include_questions": include_questions}))
	frappe.response.update({"type": "download", "filename": safe_filename(filename or title), "filecontent": pdf,
	                        "content_type": "application/pdf"})


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
