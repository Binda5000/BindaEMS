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

test('Gast sieht die Karte „Plan gegen Ist (heute)“ mit einer Tabelle', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/zeitplan');
	// die Demo-Welt hat für 09:00 bis 10:00 einen Plan (um 09:01) und die Abrechnung dazu
	const card = page.locator('section', {
		has: page.getByRole('heading', { name: 'Plan gegen Ist (heute)' })
	});
	await expect(card).toBeVisible();
	const table = card.getByRole('table');
	await expect(table).toBeVisible();
	await expect(table.getByRole('columnheader', { name: 'Differenz' })).toBeVisible();
	for (const label of ['Netzbezug', 'Einspeisung', 'Netzladen', 'Bezugskosten']) {
		await expect(table.getByRole('rowheader', { name: label })).toBeVisible();
	}
	await expect(card.locator('canvas')).toBeVisible();
	await expect(card.getByText('Plan gegen Ist ist nicht verfügbar.')).toHaveCount(0);
});

test('operator plans again', async ({ page }) => {
	const watch = watchPage(page);
	await login(page, 'bedienen');
	await page.goto('/zeitplan');
	await expect(page.getByText('Plan von').first()).toBeVisible();
	// die Gültigkeit sagt der Server, nicht die Uhr des Browsers (die Demo-Uhr steht auf dem 09.10.)
	await expect(page.getByText('gültig bis 10:45 Uhr')).toBeVisible();
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
	// Unwetter-Reserve: Das Demo-Backend kennt eine Gewitterwarnung (orange) von 18:00 bis 21:00 am
	// 09.10.; der Browser rechnet „heute“ nach seiner Uhr, darum steht der Tag davor
	await expect(
		page.getByRole('list', { name: 'Unwetterwarnungen' }).getByRole('listitem')
	).toHaveText([/^Unwetterwarnung: Gewitter \(orange\) .*18:00–21:00$/]);
	await expect(
		page
			.getByText(
				'Unwetter-Reserve: Gewitter (orange) bis 21:00 Uhr, der Akku hält mindestens 40 %.'
			)
			.first()
	).toBeVisible();
	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});
