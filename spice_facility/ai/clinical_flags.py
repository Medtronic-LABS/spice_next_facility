"""Deterministic abnormal-value flags for AI summaries.

The model writes the prose; whether a value is abnormal is decided here, in code, from the record's own
normal ranges and fixed vital-sign thresholds. That keeps alerts reproducible and explainable instead of
depending on a small model noticing them.
"""

import re

from frappe.utils import flt

# (key, label, unit, high, low): readings at/over `high` or under `low` are flagged
VITAL_LIMITS = (
	("bp_systolic", "Systolic BP", "mmHg", 140, 90),
	("bp_diastolic", "Diastolic BP", "mmHg", 90, None),
	("pulse", "Pulse", "/min", 101, 50),
	("temperature", "Temperature", "°F", 100.4, None),
	("respiratory_rate", "Respiratory rate", "/min", 25, 10),
)
NUMBER = r"-?\d+(?:\.\d+)?"


def parse_range(text, sex=None):
	"""Normal range text -> (low, high); handles "70-100", "<200", ">40", "M 13-17, F 12-15"."""
	if not text:
		return None
	text = str(text)
	if sex and re.search(r"\b[MF]\b", text):
		part = re.search(rf"\b{sex[0].upper()}\s*({NUMBER})\s*-\s*({NUMBER})", text)
		if part:
			return flt(part.group(1)), flt(part.group(2))
	if m := re.search(rf"({NUMBER})\s*-\s*({NUMBER})", text):
		return flt(m.group(1)), flt(m.group(2))
	if m := re.search(rf"<\s*=?\s*({NUMBER})", text):
		return None, flt(m.group(1))
	if m := re.search(rf">\s*=?\s*({NUMBER})", text):
		return flt(m.group(1)), None
	return None


def _number(value):
	try:
		return flt(str(value).strip()) if value not in (None, "") and re.fullmatch(NUMBER, str(value).strip()) else None
	except (TypeError, ValueError):
		return None


def lab_flags(labs, sex=None):
	flags = []
	for i, test in enumerate(labs or []):
		for j, result in enumerate(test.get("results") or []):
			value, bounds = _number(result.get("value")), parse_range(result.get("normal_range"), sex)
			if value is None or not bounds:
				continue
			low, high = bounds
			direction = "high" if high is not None and value > high else "low" if low is not None and value < low else None
			if not direction:
				continue
			span = (high - low) if low is not None and high is not None else (high or low or 1)
			distance = (value - high) if direction == "high" else (low - value)
			flags.append({
				"text": f"{result['parameter']} {value:g} {result.get('unit') or ''} ({direction}; normal {result.get('normal_range')}) — {test.get('test')}, {test.get('date') or ''}".replace("  ", " "),
				"severity": "high" if distance > 0.25 * abs(span) else "medium",
				"source": f"labs[{i}].results[{j}]", "record": test.get("record"), "parameter": result["parameter"],
			})
	return flags


def vital_flags(vitals):
	if not vitals:
		return []
	latest_index = len(vitals) - 1
	latest = vitals[latest_index]
	flags = []
	for key, label, unit, high, low in VITAL_LIMITS:
		value = latest.get(key)
		if value is None:
			continue
		if (high is not None and value >= high) or (low is not None and value < low):
			flags.append({"text": f"{label} {value:g} {unit} on {latest.get('date')}", "severity": "medium",
			              "source": f"vitals[{latest_index}].{key}", "parameter": label})
	return flags


def vital_trends(vitals):
	"""First vs latest reading per vital, so the prose can describe direction with real numbers."""
	if not vitals or len(vitals) < 2:
		return []
	first, last = vitals[0], vitals[-1]
	trends = []
	for key, label, unit, *_ in VITAL_LIMITS:
		if first.get(key) is not None and last.get(key) is not None and first[key] != last[key]:
			trends.append(f"{label} {first[key]:g} → {last[key]:g} {unit} ({first.get('date')} → {last.get('date')})")
	return trends


def flags_for(context, sex=None):
	return lab_flags(context.get("labs"), sex) + vital_flags(context.get("vitals"))
