// Ask Data chat, shared by the full page (/desk/ask-data) and the floating drawer.
// Questions are answered by a background job (spice_facility.api.talk_to_data.ask); the answer arrives on
// the `spice_ai_answer` realtime event, with polling as a fallback.
frappe.provide("spice_facility.ai");

(() => {
	const { API, POLL_MS, NARROW, waiting, esc, icon } = spice_facility.ai.ask.lib;

	class AskData {
		constructor(parent, { mode = "page", get_page = () => null } = {}) {
			this.S = spice_facility.ai.strings.chat;
			this.mode = mode;
			this.get_page = get_page;
			this.use_context = true;
			this.thread_id = this.load_thread_id();
			this.$root = $(`<div class="sf-ask sf-ask-${mode}"></div>`).appendTo(parent);
			this.init();
		}

		async init() {
			this.settings = await frappe.xcall(`${API}.settings`);
			if (!this.settings.chat_enabled) {
				this.$root.html(`<div class="sf-ask-empty">${esc(spice_facility.ai.strings.summary.disabled)}</div>`);
				return;
			}
			this.provider = this.settings.default_provider;
			this.render_shell();
			this.refresh_context();
			this.load_threads();
			if (this.thread_id) await this.open_thread(this.thread_id);
		}

		render_shell() {
			const S = this.S;
			const options = this.settings.providers.map((p) =>
				`<option value="${esc(p.name)}" ${p.name === this.provider ? "selected" : ""}>${esc(p.name)} · ${esc(p.model || "")}</option>`).join("");
			this.$root.html(`
				<aside class="sf-ask-side" aria-label="${esc(S.chats)}">
					<div class="sf-ask-side-head">
						${this.side_button("panel-left-close", S.hide_chats)}
						<span class="sf-ask-side-title">${esc(S.chats)}</span>
						${this.new_chat_button()}
					</div>
					<div class="sf-ask-threads"></div>
				</aside>
				<div class="sf-ask-main">
				<div class="sf-ask-head">
					<div class="sf-ask-head-tools">
						${this.side_button("panel-left-open", S.show_chats)}
						${this.new_chat_button()}
					</div>
					<div class="sf-ask-context"></div>
					${this.print_button()}
				</div>
				<div class="sf-ask-thread"></div>
				<div class="sf-ask-examples"></div>
				<form class="sf-ask-form">
					<div class="sf-ask-disclaimer">${esc(S.disclaimer)}</div>
					<div class="sf-ask-composer">
						<textarea class="sf-ask-input" rows="1" placeholder="${esc(S.placeholder)}" aria-label="${esc(S.placeholder)}"
							data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false"></textarea>
						<div class="sf-ask-composer-tools">
							<span class="sf-ask-where"></span>
							<select class="sf-ask-provider" title="${esc(S.provider)}" aria-label="${esc(S.provider)}">${options}</select>
							<button class="sf-ask-send" type="submit" title="${esc(S.send)}" aria-label="${esc(S.send)}">${icon("arrow-up")}</button>
						</div>
					</div>
				</form>
				</div>`);
			this.$thread = this.$root.find(".sf-ask-thread");
			this.$root.find(".sf-ask-provider").on("change", (e) => { this.provider = e.target.value; this.render_note(); });
			this.$root.find(".sf-ask-new").on("click", () => this.new_chat());
			this.$root.find(".sf-ask-side-toggle").on("click", () => this.toggle_side());
			this.$root.find(".sf-ask-print").on("click", () => this.print_chat());
			this.$root.on("click", ".sf-ask-thread-item", (e) => this.open_thread(e.currentTarget.dataset.thread));
			this.$root.on("click", ".sf-ask-thread-del", (e) => this.delete_thread(e.currentTarget.dataset.thread));
			const wide_page_default = () => this.mode === "page" && !this.overlay() && !this.side_pref_hidden();
			this.set_side(wide_page_default());
			window.matchMedia(NARROW).addEventListener("change", () => this.set_side(wide_page_default()));
			// While a question is being answered the send button is a Stop button.
			this.$root.find(".sf-ask-form").on("submit", (e) => {
				e.preventDefault();
				if (this.busy) this.stop();
				else this.ask(this.$root.find(".sf-ask-input").val());
			});
			this.$root.find(".sf-ask-input").on("keydown", (e) => {
				if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.ask(e.target.value); }
			}).on("input", (e) => this.fit_input(e.target));
			$(document).on("click", (e) => {
				if (!$(e.target).closest(".sf-ask-sources, .sf-ask-src").length) this.close_sources();
			});
			$(document).on("keydown", (e) => {
				if (e.key === "Escape") this.close_sources();
			});
			this.$root.on("click", ".sf-ai-link", (e) => {
				e.preventDefault();
				this.close_sources();
				frappe.set_route("Form", e.currentTarget.dataset.doctype, e.currentTarget.dataset.name);
			});
			this.render_note();
		}

		render_note() {
			const provider = this.settings.providers.find((p) => p.name === this.provider);
			const local = !provider || provider.is_local;
			const note = local ? this.S.local_note : this.S.cloud_note;
			this.$root.find(".sf-ask-where").attr({ title: note, "aria-label": note })
				.toggleClass("sf-cloud", !local).toggleClass("sf-local", local).html(icon(local ? "server" : "cloud"));
		}

		refresh_context() {
			const page = this.page();
			const $ctx = this.$root.find(".sf-ask-context");
			if (!page) $ctx.empty();
			else {
				$ctx.html(`<button class="sf-ask-chip ${this.use_context ? "active" : ""}" title="${esc(this.use_context ? this.S.context : this.S.context_off)}">
					${esc(page.doctype)}${page.name ? " · " + esc(page.label || page.name) : ""}</button>`);
				$ctx.find(".sf-ask-chip").on("click", () => { this.use_context = !this.use_context; this.refresh_context(); });
			}
			this.render_examples();
		}

		render_examples() {
			const page = this.use_context ? this.page() : null;
			const examples = (page && page.name && this.S.examples[page.doctype]) || this.S.examples.default;
			const $ex = this.$root.find(".sf-ask-examples");
			$ex.html(this.$thread.children().length ? "" : examples.map((q) => `<button class="sf-ask-example">${esc(q)}</button>`).join(""));
			$ex.find(".sf-ask-example").on("click", (e) => this.ask(e.currentTarget.textContent));
		}

		page() {
			return this.get_page ? this.get_page() : null;
		}

		async ask(question, { use_context = this.use_context } = {}) {
			question = (question || "").trim();
			if (!question || this.busy) return;
			this.busy = true;
			const pending = (this.pending = { stopped: false, log: null, end: null });
			this.set_sending(true);
			this.fit_input(this.$root.find(".sf-ask-input").val("").get(0));
			this.$root.find(".sf-ask-examples").empty();
			const $turn = this.render_question(question);
			const $answer = $(this.pending_html()).appendTo($turn);
			const started = Date.now();
			const tick = setInterval(() => $answer.find(".sf-ask-elapsed").text(this.S.elapsed(Math.round((Date.now() - started) / 1000))), 1000);
			this.scroll();
			try {
				const page = use_context ? this.page() : null;
				const response = await frappe.xcall(`${API}.ask`, {
					question, provider: this.provider, page: page ? JSON.stringify(page) : null, use_context: use_context ? 1 : 0,
					thread: this.thread_id,
				});
				if (response.thread !== this.thread_id) this.set_thread_id(response.thread);
				const log = response.log;
				pending.log = log;
				const result = pending.stopped
					? (this.request_stop(log), { status: "Stopped" })
					: await this.wait(log, (status) =>
						$answer.find(".sf-ask-pending-text").text(status === "Queued" ? this.S.queued : this.S.thinking), pending);
				const turn = { question, log, status: result.status, error: result.error, answer: result.answer,
				               asked_on: result.asked_on, duration_ms: result.duration_ms,
				               provider: result.provider, model: result.model };
				this.load_threads();
				$answer.replaceWith(this.answer_html(turn));
				this.after_render($turn, turn);
			} catch (error) {
				$answer.removeClass("sf-ask-pending").html(`<div class="sf-ask-error">${esc(this.S.failed)}</div>`);
			} finally {
				clearInterval(tick);
				this.busy = false;
				this.pending = null;
				this.set_sending(false);
				this.scroll();
			}
		}

		wait(log, on_status = () => {}, pending = null) {
			return new Promise((resolve) => {
				let done = false;
				const end = (data) => {
					done = true;
					waiting.delete(log);
					clearInterval(timer);
					resolve(data);
				};
				const finish = async () => {
					if (done) return;
					const data = await frappe.xcall(`${API}.get_answer`, { log });
					if (done) return;
					on_status(data.status);
					if (["Answered", "Failed", "Stopped"].includes(data.status)) end(data);
				};
				if (pending) pending.end = () => !done && end({ status: "Stopped" });
				waiting.set(log, finish);
				const timer = setInterval(finish, POLL_MS);
			});
		}

		render_question(question) {
			return $(`<div class="sf-ask-turn"><div class="sf-ask-q">${esc(question)}</div></div>`).appendTo(this.$thread);
		}

		render_turn(turn) {
			const $turn = this.render_question(turn.question);
			$turn.append(this.answer_html(turn));
			this.after_render($turn, turn);
		}

		scroll() {
			const el = this.mode === "drawer" ? this.$thread.get(0) : this.$root.get(0);
			if (el) el.scrollTop = el.scrollHeight;
			this.$thread.children().last().get(0)?.scrollIntoView({ block: "end", behavior: "smooth" });
		}
	}

	// The chat's parts live in their own files; each adds its methods to the view.
	Object.assign(AskData.prototype, spice_facility.ai.ask.answer_card, spice_facility.ai.ask.chat_list,
		spice_facility.ai.ask.composer);

	spice_facility.ai.AskData = AskData;
})();
