// Ask Data: full-page chat over the governed Talk-to-Data pipeline (see public/js/ask_data/ask_data_view.js).
frappe.pages["ask-data"].on_page_load = (wrapper) => {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: spice_facility.ai.strings.chat.title, single_column: true });
	new spice_facility.ai.AskData(page.main, { mode: "page", get_page: () => null });
};
