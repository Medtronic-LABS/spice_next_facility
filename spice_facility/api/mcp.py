"""Read-only MCP server for Talk-to-Data: `POST /api/method/spice_facility.api.mcp.mcp`.

Same transport as frappe_theme's MCP (frappe_theme/apis/mcp.py): hand-rolled JSON-RPC 2.0 returned as a raw
werkzeug Response (Frappe would otherwise wrap it in {"message": ...}), standard Frappe session / API-key
auth, never allow_guest. Unlike frappe_theme's tools, every tool here is read-only and runs as the calling
user through the same validator and executor as the in-app chat.

    claude mcp add --transport http spice-data https://<site>/api/method/spice_facility.api.mcp.mcp \\
        --header "Authorization: token <api_key>:<api_secret>"
"""

import json

import frappe
from werkzeug.wrappers import Response

from spice_facility.ai import data_catalog, query
from spice_facility.ai.config import ensure_ai_access, get_config
from spice_facility.ai.providers.base import AIProviderError
from spice_facility.ai.talk_to_data import run_question

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "spice-facility-data", "version": "1.0.0"}


def _list_datasets():
	return [{"dataset": d["doctype"], "label": d["label"], "description": d["description"]}
	        for d in data_catalog.available()]


def _describe_dataset(dataset):
	resolved = data_catalog.get(dataset)
	if not resolved:
		raise query.QueryValidationError(f"Unknown or unreadable dataset {dataset}")
	return {"dataset": dataset, "date_field": resolved["date_field"],
	        "fields": {f: {"label": m["label"], "type": m["type"]} for f, m in resolved["fields"].items()},
	        "measures": resolved["measures"], "query_spec_schema": query.spec_schema(resolved)}


def _run_query(dataset, spec):
	resolved = data_catalog.get(dataset)
	if not resolved:
		raise query.QueryValidationError(f"Unknown or unreadable dataset {dataset}")
	config = get_config()
	clean = query.validate(spec, resolved, config.max_groups)
	result = query.execute(clean, resolved, None, config.max_rows)
	shown = query.present(result, clean, resolved)
	return {"plan": query.describe(clean, resolved), "rows_read": result["rows_read"],
	        "truncated": result["truncated"], **{k: shown[k] for k in ("number", "table", "chart") if k in shown}}


def _ask(question):
	answer, _meta = run_question(question, get_config().summary_provider, None, False, None)
	return {k: answer.get(k) for k in ("text", "how", "number", "table", "chart", "rows_read") if answer.get(k) is not None}


TOOLS = {
	"list_datasets": {
		"fn": lambda args: _list_datasets(),
		"schema": {"description": "List the datasets (tables) you may query, with a short description.",
		           "inputSchema": {"type": "object", "properties": {}}},
	},
	"describe_dataset": {
		"fn": lambda args: _describe_dataset(args.get("dataset")),
		"schema": {"description": "Fields, measures and the JSON schema of a query spec for one dataset.",
		           "inputSchema": {"type": "object", "properties": {"dataset": {"type": "string"}}, "required": ["dataset"]}},
	},
	"run_query": {
		"fn": lambda args: _run_query(args.get("dataset"), args.get("spec") or {}),
		"schema": {"description": "Run a query spec (see describe_dataset) against a dataset. Read-only; returns a "
		                          "number, table and chart data.",
		           "inputSchema": {"type": "object", "properties": {"dataset": {"type": "string"}, "spec": {"type": "object"}},
		                           "required": ["dataset", "spec"]}},
	},
	"ask": {
		"fn": lambda args: _ask(str(args.get("question") or "")[:500]),
		"schema": {"description": "Ask a plain-language analytics question; planned and answered on the server's "
		                          "local model. Read-only.",
		           "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
	},
}


@frappe.whitelist(methods=["POST"])
def mcp():
	req = dict(frappe.local.form_dict)
	req.pop("cmd", None)
	if req.get("jsonrpc") != "2.0" or "method" not in req:
		return _json(_error(req.get("id"), -32600, "Invalid Request"))
	method, params, req_id = req["method"], req.get("params") or {}, req.get("id")
	if method == "initialize":
		return _json(_result(req_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
		                              "serverInfo": SERVER_INFO}))
	if method == "notifications/initialized":
		return Response(status=202)
	if method == "tools/list":
		return _json(_result(req_id, {"tools": [{"name": n, **t["schema"]} for n, t in TOOLS.items()]}))
	if method == "tools/call":
		tool = TOOLS.get(params.get("name"))
		if not tool:
			return _json(_error(req_id, -32602, f"Unknown tool {params.get('name')}"))
		try:
			ensure_ai_access()
			data = tool["fn"](params.get("arguments") or {})
			return _json(_result(req_id, {"content": [{"type": "text", "text": json.dumps(data, default=str)}],
			                              "isError": False}))
		except (query.QueryValidationError, AIProviderError, frappe.ValidationError, frappe.PermissionError) as error:
			return _json(_result(req_id, {"content": [{"type": "text", "text": str(error)}], "isError": True}))
	return _json(_error(req_id, -32601, f"Method not found: {method}"))


def _result(req_id, result):
	return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
	return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _json(payload):
	return Response(json.dumps(payload, default=str), status=200, mimetype="application/json")
