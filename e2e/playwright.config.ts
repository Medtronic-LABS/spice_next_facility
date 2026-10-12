// Ask Data end-to-end suite. E2E_BASE_URL points at a running site prepared with
// spice_facility.tests.e2e_setup.prepare (the fake model answers, so no AI provider or key is needed).
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
	testDir: "./tests",
	globalSetup: "./global-setup.ts",
	timeout: 90_000,
	expect: { timeout: 30_000 },
	fullyParallel: false,
	workers: 1,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
	use: {
		baseURL: process.env.E2E_BASE_URL || "http://localhost:8088",
		storageState: "./.auth/admin.json",
		trace: "retain-on-failure",
		...devices["Desktop Chrome"],
		viewport: { width: 1400, height: 900 },
	},
});
