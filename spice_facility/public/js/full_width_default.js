// Default every desk user to the Full Width layout. Frappe stores the layout per browser and
// starts at Compact, which caps workspaces at --page-max-width. An explicit choice made in
// Settings → Appearance writes "true"/"false", so it is never overridden here.
(() => {
	const KEY = "container_fullwidth";
	try {
		if (localStorage.getItem(KEY) !== null) return;
		localStorage.setItem(KEY, "true");
	} catch (e) {
		// Storage blocked (private mode, policy): keep Frappe's default.
		return;
	}
	$(() => frappe.ui.toolbar?.set_fullwidth_if_enabled?.());
})();
