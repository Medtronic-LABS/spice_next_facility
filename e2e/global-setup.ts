// Log in once as Administrator and keep the session for every test. The login API is called from inside the
// page, so the request goes through the browser (cookies, and Chromium resolving *.localhost) and does not
// depend on the login page's markup, which themes change.
import { mkdirSync } from "node:fs";
import { chromium, type FullConfig } from "@playwright/test";

export default async function globalSetup(config: FullConfig) {
	const { baseURL } = config.projects[0].use;
	const browser = await chromium.launch();
	const page = await browser.newPage({ baseURL });
	await page.goto("/login");
	const status = await page.evaluate(async ([usr, pwd]) => {
		const response = await fetch("/api/method/login", {
			method: "POST",
			headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
			body: new URLSearchParams({ usr, pwd }),
		});
		return response.status;
	}, [process.env.E2E_USER || "Administrator", process.env.E2E_PASSWORD || "admin"]);
	if (status !== 200) throw new Error(`Login failed with HTTP ${status}; check E2E_USER / E2E_PASSWORD`);
	mkdirSync("./.auth", { recursive: true });
	await page.context().storageState({ path: "./.auth/admin.json" });
	await browser.close();
}
