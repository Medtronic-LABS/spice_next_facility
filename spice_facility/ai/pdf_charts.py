"""Charts for the Ask Data PDF (api/talk_to_data.py: export_thread_pdf), drawn as inline SVG.

The chat draws charts in the browser (Frappe Charts); a PDF is rendered by wkhtmltopdf from HTML, so the same
answers are drawn here on the server: pie / donut slices, line and column charts. Every label is escaped; the
result is Markup so the template inserts it as SVG. Colours are the light palette of public/css/ai.css.
"""

import math
from html import escape

from markupsafe import Markup

# --sf-c1 … --sf-c6 (light theme), in the same order the chat uses them
PALETTE = ("#5b6bd5", "#8aa4d6", "#9cc5b8", "#d9b88a", "#c9a3c4", "#a8b0bd")
TEXT = "#4b5563"
GRID = "#e5e7eb"
FONT = 'font-family="Inter, Noto Sans, Arial, sans-serif"'

PIE_SIZE = 170
DONUT_HOLE = 0.55  # inner radius as a share of the outer one
CHART_W, CHART_H = 640, 200
MARGIN = {"left": 46, "right": 10, "top": 10, "bottom": 30}
Y_TICKS = 4
MAX_X_LABELS = 12
X_LABEL_CHARS = 12


def colour(i):
	return PALETTE[i % len(PALETTE)]


def _num(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _short(text, limit):
	text = str(text)
	return text if len(text) <= limit else text[: limit - 1] + "…"


def _tick(value):
	return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")


def nice_max(value):
	"""The smallest 1, 2 or 5 × 10^n at or above value, so axis ticks are round numbers."""
	if value <= 0:
		return 1
	power = 10 ** math.floor(math.log10(value))
	return next(step * power for step in (1, 2, 5, 10) if step * power >= value)


# ─── pie / donut ─────────────────────────────────────────────────────────────


def pie(values, donut=False):
	"""Slices for `values` (one colour each, in order). None when there is nothing to draw."""
	values = [max(_num(v), 0) for v in values]
	total = sum(values)
	if not total:
		return None
	r = c = PIE_SIZE / 2
	shapes, angle = [], -math.pi / 2
	for i, value in enumerate(values):
		if not value:
			continue
		share = value / total
		if share >= 0.9999:
			shapes.append(f'<circle cx="{c}" cy="{c}" r="{r}" fill="{colour(i)}"/>')
			break
		end = angle + share * 2 * math.pi
		x1, y1 = c + r * math.cos(angle), c + r * math.sin(angle)
		x2, y2 = c + r * math.cos(end), c + r * math.sin(end)
		large = 1 if share > 0.5 else 0
		shapes.append(f'<path d="M{c},{c} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large} 1 {x2:.2f},{y2:.2f} Z" '
		              f'fill="{colour(i)}" stroke="#ffffff" stroke-width="1"/>')
		angle = end
	if donut:
		shapes.append(f'<circle cx="{c}" cy="{c}" r="{r * DONUT_HOLE:.2f}" fill="#ffffff"/>')
	return Markup(f'<svg xmlns="http://www.w3.org/2000/svg" width="{PIE_SIZE}" height="{PIE_SIZE}" '
	              f'viewBox="0 0 {PIE_SIZE} {PIE_SIZE}">{"".join(shapes)}</svg>')


# ─── line / column charts ────────────────────────────────────────────────────


def _frame(labels, top):
	"""Axes, horizontal grid lines with round tick values, and the x labels (thinned when there are many)."""
	left, right, t, bottom = MARGIN["left"], CHART_W - MARGIN["right"], MARGIN["top"], CHART_H - MARGIN["bottom"]
	parts = []
	for k in range(Y_TICKS + 1):
		value = top * k / Y_TICKS
		y = bottom - (bottom - t) * k / Y_TICKS
		parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
		parts.append(f'<text x="{left - 6}" y="{y + 3:.1f}" font-size="9" text-anchor="end" fill="{TEXT}" {FONT}>'
		             f"{escape(_tick(value))}</text>")
	step = max(1, math.ceil(len(labels) / MAX_X_LABELS))
	slot = (right - left) / max(len(labels), 1)
	for i, label in enumerate(labels):
		if i % step:
			continue
		x = left + slot * (i + 0.5)
		parts.append(f'<text x="{x:.1f}" y="{bottom + 14}" font-size="9" text-anchor="middle" fill="{TEXT}" {FONT}>'
		             f"{escape(_short(label, X_LABEL_CHARS))}</text>")
	return parts, left, right, t, bottom, slot


def _wrap(parts):
	return Markup(f'<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_W}" height="{CHART_H}" '
	              f'viewBox="0 0 {CHART_W} {CHART_H}">{"".join(parts)}</svg>')


def line(labels, datasets):
	"""One line per dataset ({name, values}) over the labels (periods)."""
	values = [_num(v) for d in datasets for v in d.get("values") or []]
	if not labels or not values:
		return None
	top = nice_max(max(values))
	parts, left, _right, t, bottom, slot = _frame(labels, top)
	for i, dataset in enumerate(datasets):
		points = [(left + slot * (j + 0.5), bottom - (bottom - t) * _num(v) / top)
		          for j, v in enumerate(dataset.get("values") or [])]
		parts.append(f'<polyline fill="none" stroke="{colour(i)}" stroke-width="2" '
		             f'points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}"/>')
		parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{colour(i)}"/>' for x, y in points]
	return _wrap(parts)


def columns(labels, datasets):
	"""Vertical bars: one group per label, one bar per dataset in each group."""
	values = [_num(v) for d in datasets for v in d.get("values") or []]
	if not labels or not values:
		return None
	top = nice_max(max(values))
	parts, left, _right, _t, bottom, slot = _frame(labels, top)
	t = MARGIN["top"]
	width = slot * 0.7 / max(len(datasets), 1)
	for i, dataset in enumerate(datasets):
		for j, v in enumerate(dataset.get("values") or []):
			height = (bottom - t) * _num(v) / top
			x = left + slot * j + slot * 0.15 + width * i
			parts.append(f'<rect x="{x:.1f}" y="{bottom - height:.1f}" width="{width:.1f}" height="{height:.1f}" '
			             f'rx="1.5" fill="{colour(i)}"/>')
	return _wrap(parts)
