// Prognosen: Die Demo-Welt hat ein geladenes Lastmodell, 12 Schattentage, einen Backtest und
// ein aktives Modell (tests/ui_world.py).
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { login, watchPage } from './helpers';

const card = (page: Page, name: string) =>
	page.locator('section', { has: page.getByRole('heading', { name, exact: true }) });

test('Gast sieht alle fünf Karten mit den Werten der Demo-Welt', async ({ page }) => {
	const watch = watchPage(page);
	await login(page, 'gast');
	await page.getByRole('link', { name: 'Prognosen' }).first().click();
	await expect(page).toHaveURL(/\/prognosen$/);
	await expect(page.getByRole('heading', { level: 1, name: 'Prognosen' })).toBeVisible();

	const model = card(page, 'Lastprognose');
	await expect(model.getByText('Verfahren: Modell')).toBeVisible();
	await expect(
		model.getByText(
			'Modell aktiv: in den letzten 14 Tagen 29 % genauer als das Profil (im Schattenbetrieb).'
		)
	).toBeVisible();
	await expect(model.getByText('Modell vom 09.10.2026 03:15')).toBeVisible();
	await expect(model.getByText(/Daten 14\.08\.–09\.10\.2026 · 5.376 Viertelstunden/)).toBeVisible();
	await expect(model.getByText('Trainiert am 09.10. 03:15 mit 5376 Viertelstunden.')).toBeVisible();
	// Gäste dürfen nicht trainieren
	await expect(model.getByRole('button')).toHaveCount(0);

	const quality = card(page, 'Prognosegüte');
	await expect(quality.getByRole('columnheader', { name: 'besser als Profil' })).toBeVisible();
	await expect(quality.getByRole('cell', { name: 'Last Modell' })).toHaveCount(3);
	await expect(quality.getByRole('cell', { name: '+28 %' })).toBeVisible();
	await expect(quality.getByRole('cell', { name: '+29 %' })).toHaveCount(2);

	await expect(card(page, 'Fehler je Tag').locator('canvas')).toBeVisible();

	const backtest = card(page, 'Backtest');
	await expect(backtest.getByRole('rowheader', { name: 'Gesamt' })).toBeVisible();
	await expect(backtest.getByRole('cell', { name: '+17 %' })).toBeVisible();
	await expect(backtest.getByRole('row')).toHaveCount(1 + 9 + 1);

	const features = card(page, 'Merkmale');
	await expect(features.getByRole('row').nth(1)).toContainText('Temperatur');
	await expect(features.getByRole('row').nth(1)).toContainText('28,0 %');
	await expect(features.getByText('sensor.wohnzimmer_temperatur: nicht im Modell')).toBeVisible();

	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});

test('Zeitraum 28 Tage lädt Güte und Verlauf neu', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/prognosen');
	const quality = card(page, 'Prognosegüte');
	await expect(quality.getByRole('button', { name: '14 Tage' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	const requests = [
		page.waitForRequest((r) => r.url().includes('/api/forecast/quality?days=28')),
		page.waitForRequest((r) => r.url().includes('/api/forecast/quality/daily?days=28'))
	];
	await quality.getByRole('button', { name: '28 Tage' }).click();
	await Promise.all(requests);
	await expect(quality.getByRole('button', { name: '28 Tage' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(quality.getByRole('cell', { name: 'Last Modell' })).toHaveCount(3);
});

test('Admin startet das Training: „Training läuft …“', async ({ page }) => {
	await login(page, 'admin');
	// Das Demo-Backend ist ein geteilter Zustand: Start und Stand werden hier nachgestellt, damit
	// kein echtes Training die Demo-Welt verändert und der Lauf lange genug sichtbar bleibt.
	let started = false;
	await page.route('**/api/learning/load/train', async (route) => {
		started = true;
		await route.fulfill({ status: 202, json: { started: true } });
	});
	await page.route('**/api/learning/load', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		if (started) body.training = { ...body.training, running: true };
		await route.fulfill({ response, json: body });
	});
	await page.goto('/prognosen');
	const model = card(page, 'Lastprognose');
	await model.getByRole('button', { name: 'Jetzt neu trainieren' }).click();
	const running = model.getByRole('button', { name: 'Training läuft …' });
	await expect(running).toBeVisible();
	await expect(running).toBeDisabled();
});
