"""The governed query engine behind Talk-to-Data.

A model only fills a QuerySpec whose every name is an enum taken from the catalog. The spec is validated
here, executed with `frappe.get_list` as the asking user (so their permissions apply), aggregated in
Python, and turned into a table, a chart and drill-down filters. No SQL is ever produced by a model.
"""

from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, get_first_day, get_last_day, getdate, nowdate

from spice_facility.ai.data_catalog import DATE_TYPES, NUMBER_TYPES

OPS = ("=", "!=", ">", ">=", "<", "<=", "like", "in", "between", "is set", "is not set")
AGGS = ("count", "sum", "avg", "min", "max")
BUCKETS = ("none", "day", "week", "month", "year")
OUTPUTS = ("number", "table", "bar", "line", "pie", "donut", "list")
SORTS = ("value_desc", "value_asc", "label_asc", "label_desc")
PRESETS = ("none", "today", "yesterday", "last_7_days", "last_30_days", "this_week", "this_month", "last_month",
           "this_quarter", "this_year", "last_year", "custom")
MAX_SERIES = 6


class QueryValidationError(frappe.ValidationError):
	pass


# ─── schema the model fills ─────────────────────────────────────────────────


def spec_schema(dataset):
	fields = list(dataset["fields"])
	measures = list(dataset["measures"])
	return {
		"type": "object",
		"properties": {
			"filters": {"type": "array", "maxItems": 6, "items": {
				"type": "object",
				"properties": {"field": {"type": "string", "enum": fields}, "op": {"type": "string", "enum": list(OPS)},
				               "value": {"type": "string", "description": "For 'in' use comma separated values; for 'between' use 'from,to'."}},
				"required": ["field", "op", "value"]}},
			"date_range": {"type": "object", "properties": {
				"preset": {"type": "string", "enum": list(PRESETS)},
				"from": {"type": "string", "description": "YYYY-MM-DD when preset is custom"},
				"to": {"type": "string", "description": "YYYY-MM-DD when preset is custom"}},
				"required": ["preset"]},
			"group_by": {"type": "string", "enum": ["", *fields]},
			"time_bucket": {"type": "string", "enum": list(BUCKETS)},
			"measure": {"type": "object", "properties": {
				"agg": {"type": "string", "enum": list(AGGS)}, "field": {"type": "string", "enum": ["", *measures]}},
				"required": ["agg", "field"]},
			"sort": {"type": "string", "enum": list(SORTS)},
			"output": {"type": "string", "enum": list(OUTPUTS)},
			"list_fields": {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": fields}},
			"title": {"type": "string", "description": "Short title for the answer, under 8 words."},
		},
		"required": ["filters", "date_range", "group_by", "time_bucket", "measure", "sort", "output", "list_fields", "title"],
	}


# ─── validation ─────────────────────────────────────────────────────────────


def validate(spec, dataset, max_groups):
	if not isinstance(spec, dict):
		raise QueryValidationError(_("The query plan is not an object."))
	fields, measures = dataset["fields"], dataset["measures"]
	clean = {"dataset": dataset["doctype"], "filters": [], "title": str(spec.get("title") or "")[:80]}
	for f in spec.get("filters") or []:
		if f.get("field") not in fields:
			raise QueryValidationError(_("Unknown field {0}").format(f.get("field")))
		if f.get("op") not in OPS:
			raise QueryValidationError(_("Unsupported operator {0}").format(f.get("op")))
		clean["filters"].append({"field": f["field"], "op": f["op"], "value": str(f.get("value") or "")[:200]})
	group_by = spec.get("group_by") or ""
	if group_by and group_by not in fields:
		raise QueryValidationError(_("Cannot group by {0}").format(group_by))
	measure = spec.get("measure") or {}
	agg, mfield = measure.get("agg") or "count", measure.get("field") or ""
	if agg not in AGGS:
		raise QueryValidationError(_("Unsupported measure {0}").format(agg))
	if agg != "count" and mfield not in measures:
		raise QueryValidationError(_("{0} needs a numeric field").format(agg))
	bucket = spec.get("time_bucket") or "none"
	if bucket not in BUCKETS:
		raise QueryValidationError(_("Unsupported time bucket {0}").format(bucket))
	if bucket != "none" and not dataset["date_field"]:
		raise QueryValidationError(_("{0} has no date to group by").format(dataset["label"]))
	output = spec.get("output") if spec.get("output") in OUTPUTS else "table"
	date_range = spec.get("date_range") or {}
	preset = date_range.get("preset") if date_range.get("preset") in PRESETS else "none"
	clean.update({
		"group_by": group_by, "time_bucket": bucket, "measure": {"agg": agg, "field": mfield if agg != "count" else ""},
		"sort": spec.get("sort") if spec.get("sort") in SORTS else "value_desc", "output": output,
		"list_fields": [f for f in (spec.get("list_fields") or []) if f in fields] or dataset["list_fields"],
		"date_range": {"preset": preset, "from": date_range.get("from"), "to": date_range.get("to")},
		"limit": max_groups,
	})
	if output == "list":
		clean.update({"group_by": "", "time_bucket": "none"})
	elif output == "number":
		clean.update({"group_by": "", "time_bucket": "none"})
	return clean


# ─── filters ────────────────────────────────────────────────────────────────


def preset_range(preset, start=None, end=None):
	today = getdate(nowdate())
	if preset == "today":
		return today, today
	if preset == "yesterday":
		return add_days(today, -1), add_days(today, -1)
	if preset == "last_7_days":
		return add_days(today, -6), today
	if preset == "last_30_days":
		return add_days(today, -29), today
	if preset == "this_week":
		monday = today - timedelta(days=today.weekday())
		return monday, add_days(monday, 6)
	if preset == "this_month":
		return get_first_day(today), get_last_day(today)
	if preset == "last_month":
		first = get_first_day(add_months(today, -1))
		return first, get_last_day(first)
	if preset == "this_quarter":
		first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
		return first, get_last_day(add_months(first, 2))
	if preset == "this_year":
		return date(today.year, 1, 1), date(today.year, 12, 31)
	if preset == "last_year":
		return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
	if preset == "custom" and start and end:
		try:
			return getdate(start), getdate(end)
		except (ValueError, TypeError) as error:
			raise QueryValidationError(_("Invalid date range")) from error
	return None


def _coerce(dataset, field, value):
	ftype = dataset["fields"][field]["type"]
	if ftype in DATE_TYPES:
		try:
			return str(getdate(value))
		except (ValueError, TypeError) as error:
			raise QueryValidationError(_("{0} is not a date").format(value)) from error
	if ftype in NUMBER_TYPES or ftype == "Check":
		return flt(value)
	return value


def frappe_filters(spec, dataset, scope=None):
	doctype = dataset["doctype"]
	out = []
	for f in spec["filters"]:
		field, op, value = f["field"], f["op"], f["value"]
		if op in ("is set", "is not set"):
			out.append([doctype, field, "is", "set" if op == "is set" else "not set"])
		elif op == "in":
			out.append([doctype, field, "in", [_coerce(dataset, field, v.strip()) for v in value.split(",") if v.strip()]])
		elif op == "between":
			parts = [p.strip() for p in value.split(",")]
			if len(parts) != 2:
				raise QueryValidationError(_("'between' needs two values"))
			out.append([doctype, field, "between", [_coerce(dataset, field, p) for p in parts]])
		elif op == "like":
			out.append([doctype, field, "like", f"%{value.strip('%')}%"])
		else:
			out.append([doctype, field, op, _coerce(dataset, field, value)])
	bounds = preset_range(spec["date_range"]["preset"], spec["date_range"].get("from"), spec["date_range"].get("to"))
	if bounds and dataset["date_field"]:
		out.append([doctype, dataset["date_field"], "between", [str(bounds[0]), str(bounds[1])]])
	for field, value in (scope or {}).items():
		out.append([doctype, field, "=", value])
	return out


# ─── execution + aggregation ────────────────────────────────────────────────


def bucket_key(value, bucket):
	day = getdate(value) if value else None
	if not day:
		return None
	if bucket == "day":
		return day.isoformat()
	if bucket == "week":
		return (day - timedelta(days=day.weekday())).isoformat()
	if bucket == "month":
		return day.strftime("%Y-%m")
	return str(day.year)


def bucket_bounds(key, bucket):
	if bucket == "day":
		start = end = getdate(key)
	elif bucket == "week":
		start = getdate(key)
		end = add_days(start, 6)
	elif bucket == "month":
		start = getdate(f"{key}-01")
		end = get_last_day(start)
	else:
		start, end = date(int(key), 1, 1), date(int(key), 12, 31)
	return str(start), str(end)


def _measure(rows, agg, field):
	if agg == "count":
		return len(rows)
	values = [flt(r.get(field)) for r in rows if r.get(field) not in (None, "")]
	if not values:
		return 0
	return {"sum": sum, "min": min, "max": max}.get(agg, lambda v: sum(v) / len(v))(values)


def execute(spec, dataset, scope=None, max_rows=5000):
	doctype = dataset["doctype"]
	filters = frappe_filters(spec, dataset, scope)
	wanted = {"name"} | set(spec["list_fields"] if spec["output"] == "list" else [])
	for f in (spec["group_by"], spec["measure"]["field"]):
		if f:
			wanted.add(f)
	if spec["time_bucket"] != "none":
		wanted.add(dataset["date_field"])
	order = f"{dataset['date_field']} desc" if dataset["date_field"] else "creation desc"
	rows = frappe.get_list(doctype, filters=filters, fields=sorted(wanted), order_by=order, limit_page_length=max_rows)
	result = aggregate(rows, spec, dataset)
	result.update({"rows_read": len(rows), "truncated": len(rows) >= max_rows, "filters": filters})
	return result


def _label(dataset, field, value):
	if value in (None, ""):
		return _("(not set)")
	if dataset["fields"].get(field, {}).get("type") == "Check":
		return _("Yes") if value else _("No")
	return str(value)


def aggregate(rows, spec, dataset):
	agg, mfield = spec["measure"]["agg"], spec["measure"]["field"]
	value_label = dataset["measures"].get(mfield, _("Count")) if agg != "count" else _("Count")
	if agg != "count":
		value_label = f"{agg.title()} {value_label}"
	group, bucket = spec["group_by"], spec["time_bucket"]

	if spec["output"] == "list":
		cols = spec["list_fields"]
		return {"kind": "list", "columns": [{"field": f, "label": dataset["fields"][f]["label"],
		                                    "type": dataset["fields"][f]["type"], "options": dataset["fields"][f]["options"]}
		                                   for f in cols],
		        "rows": [{"name": r.name, **{f: r.get(f) for f in cols}} for r in rows[: spec["limit"]]]}

	if not group and bucket == "none":
		return {"kind": "number", "value": _measure(rows, agg, mfield), "value_label": value_label}

	if bucket != "none":
		series = OrderedDict()
		labels = sorted({k for k in (bucket_key(r.get(dataset["date_field"]), bucket) for r in rows) if k})
		groups = defaultdict(list)
		for r in rows:
			groups[_label(dataset, group, r.get(group)) if group else value_label].append(r)
		ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:MAX_SERIES]
		for name, members in ranked:
			by_bucket = defaultdict(list)
			for r in members:
				by_bucket[bucket_key(r.get(dataset["date_field"]), bucket)].append(r)
			series[name] = [_measure(by_bucket.get(k, []), agg, mfield) for k in labels]
		return {"kind": "series", "labels": labels, "series": series, "value_label": value_label,
		        "group_raw": {_label(dataset, group, r.get(group)): r.get(group) for r in rows} if group else {}}

	groups = defaultdict(list)
	raw = {}
	for r in rows:
		key = _label(dataset, group, r.get(group))
		groups[key].append(r)
		raw[key] = r.get(group)
	items = [(k, _measure(v, agg, mfield)) for k, v in groups.items()]
	reverse = spec["sort"] in ("value_desc", "label_desc")
	items.sort(key=(lambda kv: kv[1]) if spec["sort"].startswith("value") else (lambda kv: kv[0]), reverse=reverse)
	items = items[: spec["limit"]]
	return {"kind": "groups", "labels": [k for k, _v in items], "values": [v for _k, v in items],
	        "value_label": value_label, "group_raw": {k: raw[k] for k, _v in items}}


# ─── presentation ───────────────────────────────────────────────────────────


def route_options(filters):
	"""List-view route options from frappe filters (for "Open as list" and drill-downs)."""
	options = {}
	for _dt, field, op, value in filters:
		if op == "is":
			options[field] = ["is", value]
		elif op == "=":
			options[field] = value
		else:
			options[field] = [op, value]
	return options


def present(result, spec, dataset):
	"""Table, chart and drill-down filters for the UI, from an aggregated result."""
	doctype, base = dataset["doctype"], result["filters"]
	group, bucket = spec["group_by"], spec["time_bucket"]
	out = {"kind": result["kind"], "list_route": {"doctype": doctype, "options": route_options(base)}}

	def drill(label=None, bucket_label=None):
		extra = list(base)
		if group and label is not None:
			raw = result.get("group_raw", {}).get(label)
			extra.append([doctype, group, "is", "not set"] if raw in (None, "") else [doctype, group, "=", raw])
		if bucket_label:
			extra.append([doctype, dataset["date_field"], "between", list(bucket_bounds(bucket_label, bucket))])
		return route_options(extra)

	if result["kind"] == "list":
		out["table"] = {"columns": [{"label": c["label"], "field": c["field"], "type": c["type"], "options": c["options"]}
		                            for c in result["columns"]], "rows": result["rows"], "doctype": doctype}
		return out
	if result["kind"] == "number":
		out["number"] = {"value": result["value"], "label": result["value_label"]}
		return out
	chart_type = spec["output"] if spec["output"] in ("bar", "line", "pie", "donut") else ("line" if bucket != "none" else "bar")
	if result["kind"] == "groups":
		out["chart"] = {"type": chart_type, "labels": result["labels"],
		                "datasets": [{"name": result["value_label"], "values": result["values"]}]}
		out["drill"] = [drill(label=l) for l in result["labels"]]
		label_col = dataset["fields"][group]["label"]
		out["table"] = {"columns": [{"label": label_col, "field": "label", "type": dataset["fields"][group]["type"],
		                             "options": dataset["fields"][group]["options"]},
		                            {"label": result["value_label"], "field": "value", "type": "Float"}],
		                "rows": [{"label": result["group_raw"].get(l, l), "display": l, "value": v}
		                         for l, v in zip(result["labels"], result["values"], strict=False)]}
	else:
		if chart_type in ("pie", "donut"):
			chart_type = "line"
		out["chart"] = {"type": chart_type, "labels": result["labels"],
		                "datasets": [{"name": n, "values": v} for n, v in result["series"].items()]}
		out["drill"] = [drill(bucket_label=l) for l in result["labels"]]
		out["table"] = {"columns": [{"label": _("Period"), "field": "label", "type": "Data"}] +
		                           [{"label": n, "field": f"s{i}", "type": "Float"} for i, n in enumerate(result["series"])],
		                "rows": [{"label": l, **{f"s{i}": v[j] for i, v in enumerate(result["series"].values())}}
		                         for j, l in enumerate(result["labels"])]}
	return out


def describe(spec, dataset, scope_labels=()):
	"""The plan in words, for "How I got this"."""
	parts = [f"{dataset['label']}"]
	m = spec["measure"]
	parts.append(_("count of records") if m["agg"] == "count" else f"{m['agg']} of {dataset['measures'].get(m['field'], m['field'])}")
	if spec["group_by"]:
		parts.append(_("grouped by {0}").format(dataset["fields"][spec["group_by"]]["label"]))
	if spec["time_bucket"] != "none":
		parts.append(_("per {0}").format(spec["time_bucket"]))
	for f in spec["filters"]:
		parts.append(f"{dataset['fields'][f['field']]['label']} {f['op']} {f['value']}".strip())
	bounds = preset_range(spec["date_range"]["preset"], spec["date_range"].get("from"), spec["date_range"].get("to"))
	if bounds:
		parts.append(_("{0} from {1} to {2}").format(dataset["fields"].get(dataset["date_field"], {}).get("label", ""), bounds[0], bounds[1]))
	parts += list(scope_labels)
	return " · ".join(p for p in parts if p)
