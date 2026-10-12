// Ask Data: the question box — auto-growing input, and the send button that becomes Stop while a question runs.
// Mixed into AskData (ask_data_view.js); methods use the view's state.
frappe.provide("spice_facility.ai.ask");

(() => {
	const { API, icon, MAX_INPUT_HEIGHT, SINGLE_LINE_HEIGHT } = spice_facility.ai.ask.lib;

	spice_facility.ai.ask.composer = {
		// The box grows with each new line (Shift+Enter or wrapping) up to a cap, then scrolls.
		fit_input(el) {
			if (!el) return;
			el.style.height = "auto";
			el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`;
			el.closest(".sf-ask-composer")?.classList.toggle("sf-multiline", el.scrollHeight > SINGLE_LINE_HEIGHT);
		},

		set_sending(sending) {
			const S = this.S;
			this.$root.find(".sf-ask-send").toggleClass("sf-stop", sending)
				.attr({ title: sending ? S.stop : S.send, "aria-label": sending ? S.stop : S.send })
				.html(icon(sending ? "square" : "arrow-up"));
		},

		// Stop the question in flight: the turn ends now; the server drops the queued job or discards its answer.
		stop() {
			const pending = this.pending;
			if (!pending || pending.stopped) return;
			pending.stopped = true;
			pending.end?.();
			if (pending.log) this.request_stop(pending.log);
		},

		request_stop(log) {
			frappe.xcall(`${API}.stop`, { log }).catch(() => {
				// the turn is already shown as stopped; a late answer is simply never displayed
			});
		},
	};
})();
