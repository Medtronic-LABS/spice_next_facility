// Ask Data: the chat list — server-side conversations, open / new / delete, and showing or hiding the list.
// Mixed into AskData (ask_data_view.js); methods use the view's state.
frappe.provide("spice_facility.ai.ask");

(() => {
	const { API, STORE_PREFIX, LEGACY_PREFIX, store_key, SIDE_PREF, NARROW, esc, icon } = spice_facility.ai.ask.lib;

	spice_facility.ai.ask.chat_list = {
		async load_threads() {
			try {
				this.threads = await frappe.xcall(`${API}.threads`);
			} catch (e) {
				this.threads = [];
			}
			this.render_threads();
		},

		render_threads() {
			const S = this.S;
			const today = frappe.datetime.get_today();
			const group_of = (when) => {
				const days = frappe.datetime.get_day_diff(today, frappe.datetime.str_to_obj(when));
				return days <= 0 ? S.today : days === 1 ? S.yesterday : days <= 7 ? S.previous_7_days : S.older;
			};
			const groups = new Map();
			(this.threads || []).forEach((t) => {
				const name = group_of(t.updated);
				if (!groups.has(name)) groups.set(name, []);
				groups.get(name).push(t);
			});
			const html = [...groups].map(([name, items]) => `<div class="sf-ask-threads-group">${esc(name)}</div>
				${items.map((t) => `<div class="sf-ask-thread-row ${t.thread === this.thread_id ? "active" : ""}">
					<button class="sf-ask-thread-item" data-thread="${esc(t.thread)}" title="${esc(t.title)}">${esc(t.title)}</button>
					<button class="sf-ask-thread-del" data-thread="${esc(t.thread)}" title="${esc(S.delete_chat)}" aria-label="${esc(S.delete_chat)}">${icon("trash-2")}</button>
				</div>`).join("")}`).join("");
			this.$root.find(".sf-ask-threads").html(html || `<div class="sf-ask-threads-empty">${esc(S.no_threads)}</div>`);
		},

		async open_thread(thread_id) {
			if (this.busy) return;
			if (this.overlay()) this.set_side(false);
			let turns = [];
			try {
				turns = await frappe.xcall(`${API}.thread`, { thread: thread_id });
			} catch (e) {
				turns = [];
			}
			if (!turns.length) return this.new_chat();
			this.set_thread_id(thread_id);
			this.$thread.empty();
			turns.forEach((turn) => this.render_turn(turn));
			this.render_examples();
			this.render_threads();
			this.scroll();
		},

		new_chat() {
			if (this.busy) this.stop();
			if (this.overlay()) this.set_side(false);
			this.set_thread_id(null);
			this.$thread.empty();
			this.render_examples();
			this.render_threads();
			this.$root.find(".sf-ask-input").trigger("focus");
		},

		delete_thread(thread_id) {
			const S = this.S;
			const title = (this.threads || []).find((t) => t.thread === thread_id)?.title || "";
			frappe.confirm(S.delete_confirm(title), async () => {
				await frappe.xcall(`${API}.delete_thread`, { thread: thread_id });
				frappe.show_alert({ message: S.chat_deleted, indicator: "green" });
				if (thread_id === this.thread_id) this.new_chat();
				this.load_threads();
			});
		},

		side_button(name, label) {
			return `<button class="btn btn-xs btn-default sf-ask-icon-btn sf-ask-side-toggle" title="${esc(label)}" aria-label="${esc(label)}">${icon(name)}</button>`;
		},

		new_chat_button() {
			const label = this.S.new_chat;
			return `<button class="btn btn-xs btn-default sf-ask-icon-btn sf-ask-new" title="${esc(label)}" aria-label="${esc(label)}">${icon("square-pen")}</button>`;
		},

		// In the drawer and on narrow screens the list covers the chat; on the wide page it sits beside it.
		overlay() {
			return this.mode === "drawer" || window.matchMedia(NARROW).matches;
		},

		toggle_side() {
			const open = !this.$root.hasClass("sf-side-open");
			this.set_side(open);
			if (!this.overlay()) {
				try {
					localStorage.setItem(SIDE_PREF, open ? "0" : "1");
				} catch (e) {
					// storage blocked: the choice lasts for this page only
				}
			}
		},

		set_side(open) {
			this.$root.toggleClass("sf-side-open", open);
		},

		side_pref_hidden() {
			try {
				return localStorage.getItem(SIDE_PREF) === "1";
			} catch (e) {
				return false;
			}
		},

		// ─── download the open chat as a PDF ─────────────────────────────────
		print_button() {
			const label = this.S.download_pdf;
			return `<button class="btn btn-xs btn-default sf-ask-icon-btn sf-ask-print" title="${esc(label)}" aria-label="${esc(label)}"
				${this.thread_id ? "" : "disabled"}>${icon("printer")}</button>`;
		},

		print_chat() {
			const S = this.S;
			if (!this.thread_id) return;
			const thread = this.thread_id;
			const title = (this.threads || []).find((t) => t.thread === thread)?.title || S.title;
			const dialog = new frappe.ui.Dialog({
				title: S.download_pdf,
				fields: [
					{ fieldname: "filename", fieldtype: "Data", label: S.file_name, reqd: 1,
					  default: `${title.slice(0, 60)} - ${frappe.datetime.str_to_user(frappe.datetime.get_today())}` },
					{ fieldname: "include_questions", fieldtype: "Check", label: S.include_questions, default: 1 },
				],
				primary_action_label: S.download_pdf,
				primary_action: (values) => {
					const query = new URLSearchParams({ thread, include_questions: values.include_questions ? 1 : 0,
					                                    filename: values.filename });
					// a same-page link: the browser downloads the file (attachment) without opening an empty tab
					const link = Object.assign(document.createElement("a"), { href: `/api/method/${API}.export_thread_pdf?${query}` });
					document.body.appendChild(link);
					link.click();
					link.remove();
					dialog.hide();
				},
			});
			dialog.show();
		},

		set_thread_id(thread_id) {
			this.thread_id = thread_id;
			this.$root?.find(".sf-ask-print").prop("disabled", !thread_id);
			try {
				if (thread_id) sessionStorage.setItem(store_key(), thread_id);
				else sessionStorage.removeItem(store_key());
			} catch (e) {
				// storage blocked: the open chat is simply not remembered across reloads
			}
		},

		load_thread_id() {
			try {
				Object.keys(sessionStorage).filter((k) => k.startsWith(LEGACY_PREFIX) || (k.startsWith(STORE_PREFIX) && k !== store_key()))
					.forEach((k) => sessionStorage.removeItem(k));
				return sessionStorage.getItem(store_key());
			} catch (e) {
				return null;
			}
		},
	};
})();
