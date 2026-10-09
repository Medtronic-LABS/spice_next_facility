// Floating Ask Data button on every desk page (for users allowed to use AI). Opens a right-hand drawer
// whose questions carry the current page: the open record on a form, the list filters on a list view.
frappe.provide("spice_facility.ai");

(() => {
	const HIDDEN_ON = ["ask-data"];

	function current_page() {
		const route = frappe.get_route() || [];
		if (route[0] === "Form" && window.cur_frm && cur_frm.doc && !cur_frm.is_new()) {
			const title_field = cur_frm.meta.title_field;
			return { doctype: cur_frm.doctype, name: cur_frm.doc.name,
			         label: (title_field && cur_frm.doc[title_field]) || cur_frm.doc.name };
		}
		if (route[0] === "List" && route[1]) {
			const list = window.cur_list && cur_list.doctype === route[1] ? cur_list : null;
			return { doctype: route[1], filters: list ? list.get_filters_for_args() : [], label: route[1] };
		}
		return null;
	}

	class FloatingChat {
		constructor() {
			this.S = spice_facility.ai.strings.chat;
			this.$button = $(`<button class="sf-fab" title="${frappe.utils.escape_html(this.S.open)}">✨</button>`).appendTo(document.body);
			this.$drawer = $(`<div class="sf-drawer">
				<div class="sf-drawer-head"><b>${frappe.utils.escape_html(this.S.title)}</b><button class="btn btn-xs btn-default sf-drawer-close">✕</button></div>
				<div class="sf-drawer-body"></div></div>`).appendTo(document.body);
			this.$button.on("click", () => this.toggle(true));
			this.$drawer.find(".sf-drawer-close").on("click", () => this.toggle(false));
			frappe.router.on("change", () => this.on_route());
			this.on_route();
		}

		toggle(open) {
			this.$drawer.toggleClass("open", open);
			this.$button.toggle(!open && !this.hidden_here());
			if (open && !this.view) {
				this.view = new spice_facility.ai.AskData(this.$drawer.find(".sf-drawer-body"), { mode: "drawer", get_page: current_page });
			} else if (open && this.view?.settings) {
				this.view.refresh_context();
			}
		}

		hidden_here() {
			return HIDDEN_ON.includes((frappe.get_route() || [])[0]);
		}

		on_route() {
			// forms load after the route changes; give the form a moment before reading cur_frm
			setTimeout(() => {
				if (this.hidden_here()) this.toggle(false);
				this.$button.toggle(!this.$drawer.hasClass("open") && !this.hidden_here());
				if (this.view?.settings) this.view.refresh_context();
			}, 400);
		}
	}

	$(document).on("startup", async () => {
		try {
			const settings = await frappe.xcall("spice_facility.api.talk_to_data.settings");
			if (settings.floating_chat_enabled) spice_facility.ai.floating_chat = new FloatingChat();
		} catch (e) {
			// AI not configured for this user: no button
		}
	});
})();
