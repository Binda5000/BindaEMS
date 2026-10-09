// End-to-End-Abläufe gegen das Demo-Backend (tests/e2e/ui_server.py) mit dem gebauten UI.
// Vorher `pnpm build`; das Demo-Backend startet Playwright selbst.
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: 'tests/e2e',
	// das Demo-Backend ist ein gemeinsamer Zustand
	workers: 1,
	fullyParallel: false,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
	use: {
		baseURL: 'http://127.0.0.1:8099',
		trace: 'retain-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: {
		command: 'uv run python -m tests.e2e.ui_server --port 8099 --ui-dir ui/build',
		cwd: '..',
		url: 'http://127.0.0.1:8099/health',
		reuseExistingServer: !process.env.CI,
		timeout: 120_000
	}
});
