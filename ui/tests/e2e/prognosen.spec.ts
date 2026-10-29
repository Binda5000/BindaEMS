// Prognosen: Die Demo-Welt hat ein geladenes Lastmodell, 12 Schattentage, einen Backtest und
// ein aktives Modell (tests/ui_world.py).
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { login, watchPage } from './helpers';

const card = (page: Page, name: string) =>
	page.locator('section', { has: page.getByRole('heading', { name, exact: true }) });

test('Gast sieht die fünf Karten der Lastprognose mit den Werten der Demo-Welt', async ({
	page
}) => {
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
	await expect(
		quality.getByText('Das Lastmodell lag im Mittel 29 % näher am echten Verbrauch als das Profil.')
	).toBeVisible();
	for (const name of ['Vorlauf', 'Profil', 'Modell', 'im Plan']) {
		await expect(quality.getByRole('columnheader', { name }).first()).toBeVisible();
	}
	const bis6 = quality.getByRole('row', { name: /^bis 6 h/ }).first();
	await expect(bis6).toContainText('115 W');
	await expect(bis6).toContainText('82 W');
	await expect(bis6).toContainText('113 W');
	await expect(
		quality.getByText('Mittlere Abweichung je Viertelstunde, kleiner ist besser.')
	).toBeVisible();
	await expect(quality.getByText('PV-Prognose, bis 6 h: 240 W (4 Viertelstunden)')).toBeVisible();
	// die vollständige Tabelle ist zugeklappt, bis man sie öffnet
	const all = quality.locator('details');
	await expect(all).not.toHaveAttribute('open', '');
	await all.getByText('Alle Werte').click();
	await expect(all.getByRole('columnheader', { name: 'Viertelstunden' })).toBeVisible();
	await expect(all.getByRole('cell', { name: '+28 %' })).toBeVisible();
	await expect(all.getByRole('cell', { name: '+29 %' })).toHaveCount(2);

	await expect(card(page, 'Fehler je Tag').locator('canvas')).toBeVisible();

	const backtest = card(page, 'Backtest');
	await expect(backtest.getByRole('rowheader', { name: 'Gesamt' })).toBeVisible();
	await expect(backtest.getByRole('cell', { name: '+17 %' })).toBeVisible();
	await expect(backtest.getByRole('row')).toHaveCount(1 + 9 + 1);

	const features = card(page, 'Was das Modell nutzt');
	await expect(features.getByRole('listitem').first()).toContainText('Temperatur');
	await expect(features.getByRole('listitem').first()).toContainText('28,0 %');
	await expect(features.getByText('Aussentemperatur (Sensor)')).toHaveCount(0);
	await features.getByRole('button', { name: 'alle 14 Merkmale zeigen' }).click();
	await expect(features.getByText('Aussentemperatur (Sensor)')).toBeVisible();
	await expect(features.getByText('Wohnzimmer temperatur (Sensor): nicht im Modell')).toBeVisible();

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
	await expect(quality.getByText('Das Lastmodell lag im Mittel', { exact: false })).toBeVisible();
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

test('Gast sieht das Fahrverhalten beider Autos und die Anwesenheit der nächsten 48 Stunden', async ({
	page
}) => {
	const watch = watchPage(page);
	await login(page, 'gast');
	await page.goto('/prognosen');

	// Demo-Welt: der Tesla pendelt Mo–Fr mit 07:15 (Tessie, 56 Tage), der e-Golf hat vier Tage Daten
	const driving = card(page, 'Fahrverhalten');
	await expect(driving.getByText('Fahrverhalten berechnet am 09.10. 03:40.')).toBeVisible();
	await expect(driving.getByRole('button', { name: 'Jetzt neu berechnen' })).toHaveCount(0); // Gäste dürfen nicht neu berechnen

	// gleiche Tage stehen zusammen („Mo bis Fr“); „Tage einzeln“ löst sie auf
	await driving.getByRole('button', { name: 'Tage einzeln' }).first().click();
	const tesla = driving.getByRole('table', { name: 'Tesla Model 3 · Quelle: Tessie · 56 Tage' });
	await expect(tesla.getByRole('row')).toHaveCount(1 + 7);
	const monday = tesla.getByRole('row', { name: /^Mo / });
	await expect(monday).toContainText('8 (100 %)');
	await expect(monday).toContainText('07:15');
	await expect(monday).toContainText('meist 07:20');
	await expect(monday).toContainText('50 % (begrenzt)');
	await expect(monday).toContainText('hoch');
	await expect(tesla.getByRole('row', { name: /^Sa / })).toContainText('keine Fahrt erwartet');
	await expect(
		driving.getByText('Trefferquote 28 Tage: Abfahrt 89 % (19 Tage), Ziel 94 % (17 Tage)')
	).toBeVisible();

	const egolf = driving.getByRole('table', {
		name: 'e-Golf · Quelle: nur Ansteckvorgänge · 4 Tage'
	});
	const egolfMonday = egolf.getByRole('row', { name: /^Mo bis Fr \(aus Mo–Fr\)/ });
	await expect(egolfMonday).toContainText('08:00');
	await expect(egolfMonday).toContainText('niedrig');
	await expect(driving.getByText('Trefferquote: noch zu wenig Tage')).toBeVisible();

	const presence = card(page, 'Anwesenheit');
	await expect(
		presence.getByText('Wahrscheinlichkeit angesteckt, nächste 48 Stunden')
	).toBeVisible();
	await expect(presence.locator('canvas')).toBeVisible();

	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});

test('Admin startet die Berechnung: „Berechnung läuft …“', async ({ page }) => {
	await login(page, 'admin');
	// Wie beim Training: Start und Stand werden nachgestellt, damit kein echter Lauf die Demo-Welt
	// verändert und der Lauf lange genug sichtbar bleibt.
	let started = false;
	await page.route('**/api/learning/driving/recompute', async (route) => {
		started = true;
		await route.fulfill({ status: 202, json: { started: true } });
	});
	await page.route('**/api/learning/driving', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		if (started) body.running = true;
		await route.fulfill({ response, json: body });
	});
	await page.goto('/prognosen');
	const driving = card(page, 'Fahrverhalten');
	await driving.getByRole('button', { name: 'Jetzt neu berechnen' }).click();
	const running = driving.getByRole('button', { name: 'Berechnung läuft …' });
	await expect(running).toBeVisible();
	await expect(running).toBeDisabled();
});
