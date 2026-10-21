// Complements frappe_theme's My Theme `hide_side_bar` toggle (not editing frappe_theme itself --
// see hooks.py's fixtures/my_theme.json comment). frappe_theme's own
// public/js/mobile_view.js:hide_sidebar() only reacts to `frappe.router.on("change")`, so a
// document opened by a direct URL load or refresh -- with no prior in-app route to change *from*
// -- never gets its sidebar hidden on first render. Every navigation after that is already
// handled correctly by frappe_theme's own listener; this only needs to catch that one-time
// initial render, via a MutationObserver instead of a route event, so it works regardless of how
// the page was opened.
(function () {
	if (!frappe.boot || !frappe.boot.my_theme || frappe.boot.my_theme.hide_side_bar != 1) return;

	function hide_if_eligible() {
		const el = document.querySelector(".layout-side-section");
		if (!el) return false;

		const route = frappe.get_route && frappe.get_route();
		if (route && route[0] === "Workspaces") return false;

		$(el).hide();
		$(".sidebar-toggle-btn").hide();
		return true;
	}

	if (hide_if_eligible()) return;

	const observer = new MutationObserver(() => {
		if (hide_if_eligible()) observer.disconnect();
	});
	observer.observe(document.body, { childList: true, subtree: true });
})();
