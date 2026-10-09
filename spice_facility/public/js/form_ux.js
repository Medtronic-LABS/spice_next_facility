// Overview-tab blocks for Frappe Health forms. Each Custom HTML Block created by
// spice_facility/form_ux/apply.py runs `spice_facility.form_ux.mount(root_element, "<key>")`; the data
// comes from spice_facility.api.form_summary.get_block.
frappe.provide("spice_facility.form_ux");

(() => {
	const S = spice_facility.form_ux.strings;
	const STYLESHEET = "/assets/spice_facility/css/form_ux.css";
	const METHOD = "spice_facility.api.form_summary.get_block";

	// ─── helpers ────────────────────────────────────────────────────────────
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const date = (v) => (v ? frappe.datetime.str_to_user(v) : "");
	const money = (v, currency) => format_currency(v || 0, currency);
	const num = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d).replace(/\.0+$/, ""));

	const link = (doctype, name, label) =>
		`<a class="sf-link" data-doctype="${esc(doctype)}" data-name="${esc(name)}">${esc(label || name)}</a>`;
	const badge = (label, tone = "gray") => `<span class="sf-badge sf-${tone}">${esc(label)}</span>`;
	const tile = (label, value, tone = "") =>
		`<div class="sf-tile ${tone}"><div class="sf-tile-label">${esc(label)}</div><div class="sf-tile-value">${value}</div></div>`;
	const empty = (text) => `<div class="sf-empty">${esc(text)}</div>`;
	const section = (title, body) => `<div class="sf-section"><div class="sf-section-title">${esc(title)}</div>${body}</div>`;

	const STATUS_TONE = {
		Scheduled: "blue", Open: "blue", Confirmed: "blue", "Checked In": "orange", "Checked Out": "green",
		Closed: "green", Completed: "green", Cancelled: "red", "No Show": "red", Admitted: "orange",
		Discharged: "green", "In Treatment": "orange", Triaged: "blue", Registered: "gray",
	};
	const SEVERITY_TONE = { Severe: "red", Major: "red", Moderate: "orange", Minor: "yellow" };

	// Charts are drawn into the block's shadow root; series keep only points that have a value, so labels
	// and values never drift apart (the bug in Health's patient_history vitals chart).
	// frappe.Chart cannot draw a line through a single point (it emits broken SVG paths), so a chart
	// needs two readings; callers show a message instead.
	function line_chart(el, points, series, opts = {}) {
		const kept = points.filter((p) => series.some((s) => p[s.key] != null));
		if (kept.length < 2) return false;
		new frappe.Chart(el, {
			type: opts.type || "line",
			height: opts.height || 220,
			colors: series.map((s) => s.color),
			data: {
				labels: kept.map((p) => (p.label != null ? p.label : date(p.date))),
				datasets: series.map((s) => ({ name: s.name, values: kept.map((p) => (p[s.key] == null ? 0 : p[s.key])) })),
			},
			lineOptions: { regionFill: 0, dotSize: 5, hideDots: 0 },
			axisOptions: { xIsSeries: 1, xAxisMode: "tick" },
			barOptions: { spaceRatio: 0.5 },
		});
		return true;
	}

	function ring(percent, label) {
		const p = Math.max(0, Math.min(100, percent || 0));
		const r = 34, c = 2 * Math.PI * r;
		return `<div class="sf-ring"><svg viewBox="0 0 80 80" width="96" height="96">
			<circle cx="40" cy="40" r="${r}" class="sf-ring-track"/>
			<circle cx="40" cy="40" r="${r}" class="sf-ring-fill" stroke-dasharray="${(c * p) / 100} ${c}"/>
			<text x="40" y="44" text-anchor="middle" class="sf-ring-text">${Math.round(p)}%</text></svg>
			<div class="sf-ring-label">${esc(label)}</div></div>`;
	}

	// ─── vitals (shared by Patient, Encounter, Inpatient Record) ────────────
	const VITAL_METRICS = [
		{ key: "bp", label: S.vitals.bp, series: [
			{ key: "bp_systolic", name: S.vitals.systolic, color: "#e24c4c" },
			{ key: "bp_diastolic", name: S.vitals.diastolic, color: "#f08c3a" }] },
		{ key: "pulse", label: S.vitals.pulse, series: [{ key: "pulse", name: S.vitals.pulse, color: "#7c5cff" }] },
		{ key: "temperature", label: S.vitals.temperature, series: [{ key: "temperature", name: S.vitals.temperature, color: "#2490ef" }] },
		{ key: "respiratory_rate", label: S.vitals.respiratory_rate, series: [{ key: "respiratory_rate", name: S.vitals.respiratory_rate, color: "#14b8a6" }] },
		{ key: "bmi", label: S.vitals.bmi, series: [{ key: "bmi", name: S.vitals.bmi, color: "#64748b" }] },
	];

	function vitals_strip(v) {
		if (!v) return empty(S.vitals.empty);
		const bp = v.bp_systolic != null ? `${num(v.bp_systolic)}/${num(v.bp_diastolic)}` : "—";
		return `<div class="sf-tiles">
			${tile(S.vitals.bp, bp, v.bp_systolic >= 140 || v.bp_diastolic >= 90 ? "sf-alert" : "")}
			${tile(S.vitals.pulse, num(v.pulse), v.pulse > 100 ? "sf-alert" : "")}
			${tile(S.vitals.temperature, num(v.temperature, 1), v.temperature >= 100.4 ? "sf-alert" : "")}
			${tile(S.vitals.respiratory_rate, num(v.respiratory_rate), v.respiratory_rate > 24 ? "sf-alert" : "")}
			${tile(S.vitals.bmi, num(v.bmi, 1))}
		</div><div class="sf-muted">${esc(S.vitals.latest)} · ${date(v.date)}</div>`;
	}

	// ─── renderers ──────────────────────────────────────────────────────────
	const renderers = {
		patient_header(host, d) {
			const chips = [d.sex, d.age, d.blood_group, d.mobile].filter(Boolean).map((c) => badge(c)).join("");
			const flags = [];
			if (d.admission) flags.push(badge(`${S.patient.admitted}${d.admission.bed ? " · " + S.patient.bed(d.admission.bed) : ""}`, "orange"));
			if (d.emergency) flags.push(badge(`${S.patient.in_emergency}${d.emergency.triage_level ? " · " + d.emergency.triage_level : ""}`, "red"));
			const allergies = d.allergies.length
				? d.allergies.map((a) => badge(`${a.allergy}${a.severity ? " · " + a.severity : ""}`, SEVERITY_TONE[a.severity] || "red")).join("")
				: badge(S.patient.no_allergies, "green");
			const meds = d.medications.length
				? `<ul class="sf-list">${d.medications.map((m) => `<li><b>${esc(m.medication)}</b> <span class="sf-muted">${esc([m.dosage, m.period].filter(Boolean).join(" · "))}</span></li>`).join("")}</ul>`
				: empty(S.patient.no_medications);
			const insurance = d.policy
				? `${esc(d.policy.insurance_payor)} <span class="sf-muted">${esc(S.patient.expires(date(d.policy.policy_expiry_date)))}</span>`
				: `<span class="sf-muted">${esc(S.patient.no_insurance)}</span>`;
			host.innerHTML = `
				<div class="sf-hero">
					<div class="sf-avatar">${esc(frappe.get_abbr(d.patient_name))}</div>
					<div class="sf-hero-main">
						<div class="sf-hero-name">${esc(d.patient_name)} ${flags.join("")}</div>
						<div class="sf-chips">${chips}</div>
					</div>
					<div class="sf-tiles sf-hero-tiles">
						${tile(S.patient.last_visit, d.last_visit ? `${date(d.last_visit.encounter_date)}<div class="sf-muted">${esc(d.last_visit.practitioner_name || "")}</div>` : "—")}
						${tile(S.patient.next_visit, d.next_visit ? `${date(d.next_visit.appointment_date)}<div class="sf-muted">${esc(d.next_visit.practitioner_name || "")}</div>` : "—")}
						${tile(S.patient.outstanding, money(d.outstanding, d.currency), d.outstanding > 0 ? "sf-alert" : "")}
					</div>
				</div>
				<div class="sf-grid-3">
					${section(S.patient.allergies, `<div class="sf-chips">${allergies}</div>`)}
					${section(S.patient.medications, meds)}
					${section(S.patient.insurance, insurance)}
				</div>`;
		},

		vitals_trend(host, d) {
			if (!d.points.length) return (host.innerHTML = empty(S.vitals.empty));
			host.innerHTML = `${vitals_strip(d.points[d.points.length - 1])}
				<div class="sf-toggle">${VITAL_METRICS.map((m, i) => `<button class="sf-pill${i ? "" : " active"}" data-metric="${m.key}">${esc(m.label)}</button>`).join("")}</div>
				<div class="sf-chart"></div>`;
			const chart_el = host.querySelector(".sf-chart");
			const draw = (key) => {
				const metric = VITAL_METRICS.find((m) => m.key === key);
				chart_el.innerHTML = "";
				if (!line_chart(chart_el, d.points, metric.series)) {
					const readings = d.points.filter((p) => metric.series.some((s) => p[s.key] != null)).length;
					chart_el.innerHTML = empty(readings ? S.vitals.single : S.vitals.empty);
				}
			};
			host.querySelectorAll(".sf-pill").forEach((btn) => btn.addEventListener("click", () => {
				host.querySelectorAll(".sf-pill").forEach((b) => b.classList.toggle("active", b === btn));
				draw(btn.dataset.metric);
			}));
			// Open on the vital that moved most (relative range), so the story shows without a click.
			const spread = (m) => {
				const values = d.points.map((p) => p[m.series[0].key]).filter((v) => v != null);
				if (values.length < 2) return -1;
				const lo = Math.min(...values), hi = Math.max(...values);
				return (hi - lo) / (hi || 1);
			};
			const first = VITAL_METRICS.reduce((best, m) => (spread(m) > spread(best) ? m : best), VITAL_METRICS[0]);
			host.querySelectorAll(".sf-pill").forEach((b) => b.classList.toggle("active", b.dataset.metric === first.key));
			draw(first.key);
		},

		encounter_history(host, d) {
			const alert = d.allergies.length
				? `<div class="sf-banner sf-red"><b>${esc(S.encounter.allergy_alert)}:</b> ${d.allergies.map((a) => esc(`${a.allergy} (${a.severity})`)).join(", ")}</div>` : "";
			const timeline = d.encounters.length
				? `<div class="sf-timeline">${d.encounters.map((e) => `
					<div class="sf-tl-item"><div class="sf-tl-dot"></div><div class="sf-tl-body">
						<div><b>${date(e.encounter_date)}</b> · ${link("Patient Encounter", e.name, e.practitioner_name || e.name)} <span class="sf-muted">${esc(e.medical_department || "")}</span></div>
						<div class="sf-chips">${e.diagnoses.map((x) => badge(x, "blue")).join("")}</div>
						${e.encounter_comment ? `<div class="sf-muted sf-clamp">${esc(e.encounter_comment)}</div>` : ""}
					</div></div>`).join("")}</div>`
				: empty(S.encounter.first_visit);
			host.innerHTML = `${alert}${section(S.encounter.previous, timeline)}`;
		},

		inpatient_stay(host, d) {
			if (!d.admitted) return (host.innerHTML = empty(S.stay.not_admitted));
			const day = Math.max(1, Math.ceil(d.days));
			const over = d.expected_days && d.days > d.expected_days ? Math.ceil(d.days - d.expected_days) : 0;
			const pct = d.expected_days ? (100 * d.days) / d.expected_days : 0;
			const steps = [`<div class="sf-tl-item"><div class="sf-tl-dot sf-dot-blue"></div><div class="sf-tl-body"><b>${esc(S.stay.admitted)}</b> · ${frappe.datetime.str_to_user(d.admitted)}</div></div>`];
			d.occupancies.forEach((o) => steps.push(`<div class="sf-tl-item"><div class="sf-tl-dot ${o.current ? "sf-dot-orange" : ""}"></div><div class="sf-tl-body">
				${esc(S.stay.moved_to(o.service_unit))} · ${frappe.datetime.str_to_user(o.check_in)}${o.check_out ? " → " + frappe.datetime.str_to_user(o.check_out) : ""}
				${o.current ? badge(S.stay.still_admitted, "orange") : ""}</div></div>`));
			if (d.discharged) steps.push(`<div class="sf-tl-item"><div class="sf-tl-dot sf-dot-green"></div><div class="sf-tl-body"><b>${esc(S.stay.discharged)}</b> · ${frappe.datetime.str_to_user(d.discharged)}</div></div>`);
			host.innerHTML = `
				<div class="sf-row-between">
					<div><div class="sf-big">${esc(d.expected_days ? S.stay.day_of(day, d.expected_days) : S.stay.day(day))}</div>
						${d.practitioner ? `<div class="sf-muted">${esc(S.stay.attending)}: ${esc(d.practitioner)}</div>` : ""}</div>
					${badge(d.status, STATUS_TONE[d.status] || "gray")}
				</div>
				${d.expected_days ? `<div class="sf-progress"><div class="sf-progress-bar ${over ? "sf-bg-red" : ""}" style="width:${Math.min(100, pct)}%"></div></div>` : ""}
				${over ? `<div class="sf-banner sf-red">${esc(S.stay.over(over))}</div>` : ""}
				<div class="sf-timeline">${steps.join("")}</div>`;
		},

		practitioner_day(host, d) {
			const counts = {};
			d.appointments.forEach((a) => (counts[a.status] = (counts[a.status] || 0) + 1));
			const list = d.appointments.length
				? `<div class="sf-agenda">${d.appointments.map((a) => `<div class="sf-agenda-row">
					<div class="sf-agenda-time">${esc((a.appointment_time || "").slice(0, 5))}</div>
					<div class="sf-agenda-main">${link("Patient Appointment", a.name, a.patient_name)} <span class="sf-muted">${esc(a.appointment_type || "")}</span></div>
					${badge(a.status, STATUS_TONE[a.status] || "gray")}</div>`).join("")}</div>`
				: empty(S.practitioner.no_appointments);
			host.innerHTML = `
				<div class="sf-row-between"><div class="sf-big">${esc(S.practitioner.todays_patients)} · ${d.appointments.length}</div>
					<div class="sf-chips">${Object.entries(counts).map(([s, n]) => badge(`${s}: ${n}`, STATUS_TONE[s] || "gray")).join("")}</div></div>
				${list}
				<div class="sf-muted">${esc(S.practitioner.upcoming_week(d.upcoming_week))}</div>`;
		},

		emergency_triage(host, d) {
			const color = (d.level && d.level.color) || "var(--gray-500)";
			const timers = d.timers;
			const vitals = d.vitals.length
				? `<table class="sf-table"><tbody>${d.vitals.map((v) => `<tr><td>${esc(v.observation_template)}</td><td><b>${esc(v.result_data || "—")}</b> ${esc(v.permitted_unit || "")}</td><td class="sf-muted">${frappe.datetime.str_to_user(v.creation)}</td></tr>`).join("")}</tbody></table>`
				: empty(S.emergency.no_vitals);
			host.innerHTML = `
				<div class="sf-triage" style="--sf-triage:${esc(color)}">
					<div class="sf-triage-level">${esc(d.triage_level || S.emergency.not_triaged)}</div>
					<div class="sf-triage-meta">${badge(d.status, STATUS_TONE[d.status] || "gray")} ${esc(d.arrival_mode || "")}
						${d.level && d.level.target_reassessment_mins ? `<span class="sf-muted"> · ${esc(S.emergency.reassess_every(d.level.target_reassessment_mins))}</span>` : ""}</div>
					${d.chief_complaint ? `<div class="sf-triage-complaint">${esc(d.chief_complaint)}</div>` : ""}
				</div>
				<div class="sf-tiles">
					${tile(S.emergency.door_to_triage, timers.door_to_triage != null ? esc(S.common.minutes(timers.door_to_triage)) : "—")}
					${tile(timers.closed ? S.emergency.total_time : S.emergency.in_department,
						timers.in_department != null ? esc(`${Math.floor(timers.in_department / 60)}h ${timers.in_department % 60}m`) : "—",
						!timers.closed && timers.in_department > 240 ? "sf-alert" : "")}
					${d.disposition ? tile(S.emergency.disposition, esc(d.disposition)) : ""}
				</div>
				${section(S.emergency.vitals, vitals)}`;
		},

		lab_previous_results(host, d) {
			if (d.columns.length < 2) return (host.innerHTML = empty(S.lab.no_history));
			const head = d.columns.map((c) => `<th>${c.current ? esc(S.lab.this_test) : link("Lab Test", c.name, date(c.date))}</th>`).join("");
			const body = d.rows.map((r) => {
				const current = r.values[d.columns[0].name];
				return `<tr><td><b>${esc(r.name)}</b> <span class="sf-muted">${esc(r.uom || "")}</span></td>${d.columns.map((c, i) => {
					const v = r.values[c.name];
					const changed = i > 0 && v != null && current != null && String(v) !== String(current);
					return `<td class="${i === 0 ? "sf-current" : changed ? "sf-changed" : ""}">${esc(v == null ? "—" : v)}</td>`;
				}).join("")}<td class="sf-muted">${esc(r.normal_range || "")}</td></tr>`;
			}).join("");
			host.innerHTML = `<table class="sf-table"><thead><tr><th>${esc(S.lab.parameter)}</th>${head}<th>${esc(S.lab.normal_range)}</th></tr></thead><tbody>${body}</tbody></table>`;
		},

		observation_trend(host, d) {
			const numeric = d.points.filter((p) => p.value != null);
			if (!numeric.length) return (host.innerHTML = empty(S.observation.no_numeric));
			host.innerHTML = `<div class="sf-muted">${esc(S.observation.trend(d.template))}${d.unit ? ` (${esc(d.unit)})` : ""}</div><div class="sf-chart"></div>`;
			const chart_el = host.querySelector(".sf-chart");
			if (!line_chart(chart_el, numeric, [{ key: "value", name: d.template, color: "#2490ef" }])) {
				chart_el.innerHTML = `<div class="sf-tiles">${tile(d.template, `${num(numeric[0].value, 2)} ${esc(d.unit || "")}`)}</div><div class="sf-muted">${esc(S.vitals.single)}</div>`;
			}
		},

		therapy_progress(host, d) {
			const pct = d.total ? (100 * d.completed) / d.total : 0;
			const first = d.assessments[0], last = d.assessments[d.assessments.length - 1];
			const score = (a) => (a ? `${a.total_score_obtained}${a.total_score ? "/" + a.total_score : ""}` : "—");
			host.innerHTML = `
				<div class="sf-row">
					${ring(pct, `${S.common.of(d.completed, d.total)} ${S.therapy.sessions.toLowerCase()} ${S.therapy.completed}`)}
					<div class="sf-tiles">
						${tile(S.therapy.baseline, score(first))}
						${tile(S.therapy.latest, score(last), last && first && last.total_score_obtained > first.total_score_obtained ? "sf-good" : "")}
					</div>
				</div>
				${section(S.therapy.exercise_completion, d.sessions.length ? '<div class="sf-chart"></div>' : empty(S.therapy.no_sessions))}`;
			if (d.sessions.length) {
				const chart_el = host.querySelector(".sf-chart");
				const bars = d.sessions.map((s) => ({ ...s, label: date(s.date) }));
				if (!line_chart(chart_el, bars, [{ key: "completion", name: S.therapy.exercise_completion, color: "#14b8a6" }], { type: "bar", height: 200 })) {
					chart_el.innerHTML = `<div class="sf-tiles">${tile(bars[0].label, `${num(bars[0].completion)}%`)}</div>`;
				}
			}
		},

		insurance_utilisation(host, d) {
			const expiry = d.days_to_expiry == null ? "" : d.days_to_expiry < 0 ? badge(S.insurance.expired, "red")
				: badge(S.insurance.expires_in(d.days_to_expiry), d.days_to_expiry < 30 ? "orange" : "green");
			host.innerHTML = `
				<div class="sf-row-between"><div class="sf-big">${esc(d.payor)} <span class="sf-muted">${esc(d.policy_number || "")}</span></div>${expiry}</div>
				<div class="sf-tiles">
					${tile(S.insurance.covered, money(d.covered, d.currency))}
					${tile(S.insurance.claimed, money(d.claimed, d.currency))}
					${tile(S.insurance.approved, money(d.approved, d.currency), "sf-good")}
					${tile(S.insurance.paid, money(d.paid, d.currency))}
					${tile(S.insurance.outstanding, money(d.outstanding, d.currency), d.outstanding > 0 ? "sf-alert" : "")}
				</div>
				${section(S.insurance.coverages, `<div class="sf-chips">${Object.entries(d.coverage_status).map(([s, n]) => badge(`${s}: ${n}`, STATUS_TONE[s] || "blue")).join("") || esc(S.common.none)}</div>`)}`;
		},

		claim_breakdown(host, d) {
			const approved_unpaid = Math.max(0, d.approved - d.paid);
			const pending = Math.max(0, d.claimed - d.approved - d.rejected);
			const parts = [
				{ label: S.insurance.paid, value: d.paid, color: "#22c55e" },
				{ label: S.insurance.approved, value: approved_unpaid, color: "#2490ef" },
				{ label: S.insurance.pending, value: pending, color: "#f59e0b" },
				{ label: S.insurance.rejected, value: d.rejected, color: "#ef4444" },
			].filter((p) => p.value > 0);
			// A stacked bar rather than a frappe.Chart donut, which fails to tear down inside a shadow root.
			const total = parts.reduce((sum, p) => sum + p.value, 0) || 1;
			host.innerHTML = `
				<div class="sf-stack">${parts.map((p) => `<div class="sf-stack-part" style="width:${(100 * p.value) / total}%;background:${p.color}" title="${esc(p.label)}"></div>`).join("")}</div>
				<div class="sf-chips">${parts.map((p) => `<span class="sf-legend"><i style="background:${p.color}"></i>${esc(p.label)} · ${money(p.value, d.currency)}</span>`).join("")}</div>
				<div class="sf-tiles">
					${tile(S.insurance.claimed, money(d.claimed, d.currency))}
					${tile(S.insurance.approved, money(d.approved, d.currency), "sf-good")}
					${tile(S.insurance.paid, money(d.paid, d.currency))}
					${tile(S.insurance.outstanding, money(d.outstanding, d.currency), d.outstanding > 0 ? "sf-alert" : "")}
					${tile(S.insurance.rejected, money(d.rejected, d.currency), d.rejected > 0 ? "sf-alert" : "")}
				</div>
				${section(S.insurance.coverages, `<div class="sf-chips">${Object.entries(d.coverage_status).map(([st, n]) => badge(`${st}: ${n}`, STATUS_TONE[st] || "blue")).join("")}</div>`)}`;
		},

		unit_occupancy(host, d) {
			if (!d.beds.length) return (host.innerHTML = empty(S.unit.no_beds));
			host.innerHTML = `
				<div class="sf-chips">${badge(`${S.unit.occupied}: ${d.occupied}`, "red")}${badge(`${S.unit.vacant}: ${d.vacant}`, "green")}</div>
				<div class="sf-beds">${d.beds.map((b) => `<div class="sf-bed ${b.inpatient_record ? "sf-bed-occupied" : "sf-bed-vacant"}">
					<div class="sf-bed-name">${link("Healthcare Service Unit", b.name, b.label)}</div>
					${b.inpatient_record
						? `<div>${link("Inpatient Record", b.inpatient_record, b.patient)}</div><div class="sf-muted">${esc(S.unit.since(frappe.datetime.str_to_user(b.since)))}</div>`
						: `<div class="sf-muted">${esc(S.unit.vacant)}</div>`}
				</div>`).join("")}</div>`;
		},
	};

	// ─── mount ──────────────────────────────────────────────────────────────
	// Blocks that load their own data (e.g. the AI summary) register here instead of in `renderers`.
	spice_facility.form_ux.custom_mounts = spice_facility.form_ux.custom_mounts || {};
	spice_facility.form_ux.helpers = { esc, date, badge, tile, empty, section, link };

	spice_facility.form_ux.mount = async (root, key) => {
		const host = root.querySelector(".sf-block");
		const frm = window.cur_frm;
		const custom = spice_facility.form_ux.custom_mounts[key];
		if (!host || !frm || (!renderers[key] && !custom)) return;

		// `root` is the block's ShadowRoot, so the stylesheet is added as a node, not as HTML.
		if (!root.querySelector(`link[href="${STYLESHEET}"]`)) {
			const sheet = document.createElement("link");
			sheet.rel = "stylesheet";
			sheet.href = STYLESHEET;
			root.prepend(sheet);
		}
		// Links live in a shadow root, where the desk router cannot see clicks; route them here.
		host.addEventListener("click", (e) => {
			const a = e.target.closest(".sf-link");
			if (!a) return;
			e.preventDefault();
			frappe.set_route("Form", a.dataset.doctype, a.dataset.name);
		});

		if (frm.is_new()) {
			host.innerHTML = empty(S.common.save_first);
			return;
		}
		if (custom) return custom(host, frm);
		host.innerHTML = `<div class="sf-muted">${esc(S.common.loading)}</div>`;
		try {
			const data = await frappe.xcall(METHOD, { block: key, doctype: frm.doctype, name: frm.doc.name });
			renderers[key](host, data, frm);
		} catch (error) {
			console.error(`spice_facility: block ${key} failed`, error);
			host.innerHTML = empty(S.common.load_failed);
		}
	};
})();
