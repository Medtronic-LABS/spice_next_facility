// Ask Data, end to end, with the fake model (spice_facility/ai/providers/fake.py): its plans are scripted by
// keyword, and a question containing "[slow]" keeps planning for 20 s so it can be stopped while running.
import { expect, test, type Page } from "@playwright/test";

const SLOW_PLANNING_MS = 20_000; // fake.py SLOW_SECONDS
// Questions carry a per-run suffix so a retry never meets the chats an earlier attempt left behind.
const RUN = Date.now().toString(36);
const q = (text: string) => `${text} #${RUN}`;

// Status of the open chat's latest question, read through the same API the page uses.
async function lastStatus(page: Page) {
	return page.evaluate(async () => {
		// null until the new chat has its id (the ask request is still in flight)
		const thread = sessionStorage.getItem(`spice_ai_thread::${frappe.session.user}`);
		if (!thread) return null;
		const turns = await frappe.xcall("spice_facility.api.talk_to_data.thread", { thread });
		return turns[turns.length - 1]?.status ?? null;
	});
}

async function openAskData(page: Page) {
	await page.goto("/desk/ask-data");
	await expect(page.locator(".sf-ask-input")).toBeVisible();
	await page.locator(".sf-ask-new:visible").first().click();
}

async function ask(page: Page, question: string) {
	await page.locator(".sf-ask-input").fill(question);
	await page.keyboard.press("Enter");
}

const lastAnswer = (page: Page) => page.locator(".sf-ask-answer").last();

test("answers with a card, the model tag and a Sources panel", async ({ page }) => {
	await openAskData(page);
	await ask(page, "Encounters by department");
	await expect(lastAnswer(page).locator(".sf-ask-title")).toContainText("(fake)");
	await expect(lastAnswer(page).locator(".sf-ask-model")).toContainText("FAKE");

	await lastAnswer(page).locator(".sf-ask-src").click();
	const sources = lastAnswer(page).locator(".sf-ask-sources");
	await expect(sources).toBeVisible();
	await expect(sources).toContainText("count of records");
	await page.keyboard.press("Escape");
	await expect(sources).toBeHidden();
});

test("a running question can be stopped, stays stopped, and can be resent", async ({ page }) => {
	test.setTimeout(3 * SLOW_PLANNING_MS + 30_000);
	await openAskData(page);
	await ask(page, q("Encounters by department [slow]"));
	await expect(page.locator(".sf-ask-send.sf-stop")).toBeVisible();
	await expect.poll(() => lastStatus(page)).toBe("Running"); // the job has started, so its answer must be discarded

	await page.locator(".sf-ask-send").click();
	await expect(lastAnswer(page)).toHaveClass(/sf-ask-stopped/);
	await expect(page.locator(".sf-ask-send.sf-stop")).toHaveCount(0);

	// The job finishes planning after the stop: it must leave the question Stopped, with no answer.
	await page.waitForTimeout(SLOW_PLANNING_MS + 5_000);
	expect(await lastStatus(page)).toBe("Stopped");
	await page.reload();
	await expect(lastAnswer(page)).toHaveClass(/sf-ask-stopped/);

	await lastAnswer(page).locator(".sf-ask-resend").click();
	await expect(lastAnswer(page).locator(".sf-ask-title")).toContainText("(fake)", { timeout: SLOW_PLANNING_MS + 30_000 });
});

test("chats are listed, reopened, deleted, and the list can be hidden", async ({ page }) => {
	await openAskData(page);
	await ask(page, q("How many encounters"));
	await expect(lastAnswer(page).locator(".sf-ask-kpi")).toBeVisible();
	await page.locator(".sf-ask-new:visible").first().click();
	await ask(page, q("Lab tests by status"));
	await expect(lastAnswer(page).locator(".sf-ask-title")).toContainText("Lab Test");

	const list = page.locator(".sf-ask-threads");
	await expect(list).toContainText(q("How many encounters"));
	await list.locator(".sf-ask-thread-item", { hasText: q("How many encounters") }).click();
	await expect(page.locator(".sf-ask-q").first()).toHaveText(q("How many encounters"));

	const row = list.locator(".sf-ask-thread-row", { hasText: q("Lab tests by status") });
	await row.hover();
	await row.locator(".sf-ask-thread-del").click();
	await page.locator(".modal.show button", { hasText: "Yes" }).click();
	await expect(list.locator(".sf-ask-thread-row", { hasText: q("Lab tests by status") })).toHaveCount(0);

	await page.locator(".sf-ask-side .sf-ask-side-toggle").click();
	await expect(page.locator(".sf-ask")).not.toHaveClass(/sf-side-open/);
	await page.reload();
	await expect(page.locator(".sf-ask-head-tools .sf-ask-side-toggle")).toBeVisible(); // still hidden
	await page.locator(".sf-ask-head-tools .sf-ask-side-toggle").click();
	await expect(page.locator(".sf-ask")).toHaveClass(/sf-side-open/);
});

test("the floating button opens Ask Data on a record, with that record as context", async ({ page }) => {
	await page.goto("/desk/patient/SF%20Test%20Patient");
	await page.locator(".sf-fab").click();
	const drawer = page.locator(".sf-drawer.open");
	await expect(drawer.locator(".sf-ask-chip")).toContainText("SF Test Patient");
	await drawer.locator(".sf-ask-new:visible").first().click();
	await drawer.locator(".sf-ask-input").fill("How many encounters");
	await drawer.locator(".sf-ask-input").press("Enter");
	await expect(drawer.locator(".sf-ask-answer").last().locator(".sf-ask-kpi")).toBeVisible();
});

test("a chat downloads as a PDF under the chosen name, with or without the questions", async ({ page }) => {
	await openAskData(page);
	await ask(page, q("Print me"));
	await expect(lastAnswer(page).locator(".sf-ask-kpi")).toBeVisible();
	await ask(page, "Encounters by department");
	await expect(lastAnswer(page).locator(".sf-ask-title")).toContainText("(fake)");

	const row = page.locator(".sf-ask-thread-row", { hasText: q("Print me") });
	await row.hover();
	await row.locator(".sf-ask-thread-print").click();
	const dialog = page.getByRole("dialog").filter({ hasText: "Include questions" });
	await dialog.locator('input[data-fieldname="filename"]').fill("Ward report");
	await dialog.locator('input[data-fieldname="include_questions"]').uncheck();
	const [download] = await Promise.all([
		page.waitForEvent("download"),
		dialog.getByRole("button", { name: "Download PDF" }).click(),
	]);
	expect(download.suggestedFilename()).toBe("Ward report.pdf");
	const stream = await download.createReadStream();
	const head = await new Promise<string>((resolve) => stream.once("data", (chunk) => resolve(String(chunk).slice(0, 4))));
	expect(head).toBe("%PDF");
});
