// Zeitplan: Die Demo-Welt plant beim Start einmal; „Neu planen“ läuft über den echten Dienst.
import { expect, test } from '@playwright/test';
import { login, watchPage } from './helpers';

test('Gast sieht den Plan ohne Bedienung', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/zeitplan');
	await expect(page.getByRole('heading', { level: 1, name: 'Zeitplan' })).toBeVisible();
	await expect(page.getByText('Plan von').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Ablauf' })).toBeVisible();
	await expect(page.getByRole('switch', { name: 'Netzladen erlaubt' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Neu planen' })).toHaveCount(0);
});

test('operator plans again', async ({ page }) => {
	const watch = watchPage(page);
	await login(page, 'bedienen');
	await page.goto('/zeitplan');
	await expect(page.getByText('Plan von').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Ablauf' })).toBeVisible();
	await expect(page.locator('canvas').first()).toBeVisible();

	const reply = page.waitForResponse(
		(response) =>
			response.url().endsWith('/api/plan/replan') && response.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Neu planen' }).click();
	expect((await reply).status()).toBe(202);

	// der Lauf endet mit einem Plan, ohne Fehlermeldung
	await expect(page.getByRole('button', { name: 'Neu planen' })).toBeEnabled({ timeout: 45_000 });
	await expect(page.getByText('Plan von').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Ablauf' })).toBeVisible();
	await expect(page.locator('.notice.error')).toHaveCount(0);
	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});
