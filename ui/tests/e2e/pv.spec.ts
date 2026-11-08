import { expect, test, type Page } from '@playwright/test';
import { T_DEMO, login } from './helpers';

interface Source {
	id: number;
	name: string;
	parent_id: number | null;
}

/** Einträge eines früheren Laufs (Demo-Backend wiederverwendet) löschen: erst Strings, dann Anlagen */
async function removeLeftovers(page: Page): Promise<void> {
	const response = await page.request.get('/api/pv');
	const { sources } = (await response.json()) as { sources: Source[] };
	const cookies = await page.context().cookies();
	const csrf = cookies.find((cookie) => cookie.name === 'bindaems_csrf')?.value ?? '';
	const order = [...sources].sort(
		(a, b) => Number(a.parent_id === null) - Number(b.parent_id === null)
	);
	for (const source of order) {
		await page.request.delete(`/api/pv/${source.id}`, { headers: { 'X-CSRF-Token': csrf } });
	}
}

async function noHorizontalScroll(page: Page): Promise<number> {
	return page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
}

test('PV-Anlagen: Anlage und String anlegen, Jahr ansehen, wieder löschen', async ({ page }) => {
	await login(page, 'admin');
	await removeLeftovers(page);
	await page.clock.setFixedTime(T_DEMO); // „heute“ ist der 09.10.2026, wie in der Demo-Welt
	await page.goto('/pv');
	await expect(page.getByRole('heading', { name: 'PV-Anlagen', level: 1 })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Fr., 09.10.2026' })).toBeVisible();
	await expect(page.getByText(/Hier erscheint je PV-Anlage eine Karte/)).toBeVisible();

	// Anlage „Huawei“ am AC-Zähler des Victron
	await page.getByRole('button', { name: 'Anlage hinzufügen' }).click();
	const system = page.getByRole('dialog', { name: 'Anlage hinzufügen' });
	await system.getByLabel('Name').fill('Huawei');
	await system.getByLabel('core-Signal').fill('pv.huawei.power_w');
	await system.getByLabel(/^kWp/).fill('9,8');
	await system.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText('„Huawei“ angelegt.')).toBeVisible();
	const card = page.locator('section').filter({
		has: page.getByRole('heading', { name: 'Huawei', level: 2 })
	});
	await expect(card.getByText('9,80 kWp · Victron-Zähler (AC)')).toBeVisible();

	// String „Süd“ aus Home Assistant
	await card.getByRole('button', { name: 'String hinzufügen' }).click();
	const string = page.getByRole('dialog', { name: 'String hinzufügen' });
	await expect(string.getByLabel('Gehört zu')).toHaveValue(/\d+/);
	await string.getByLabel('Name').fill('Süd');
	await string.getByRole('radio', { name: 'Home Assistant' }).check();
	await string.getByLabel('HA-Entität').fill('sensor.pv1_leistung');
	await string.getByLabel('Einheit').selectOption('W');
	await string.getByLabel(/^kWp/).fill('4,1');
	await string.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText('„Süd“ angelegt.')).toBeVisible();

	// die Karte zeigt beide; die Demo rechnet ihre Energie alle 5 s nach
	await expect(card.getByText('Strings im Vergleich (kWh je kWp)')).toBeVisible();
	await expect(card.locator('.string').filter({ hasText: 'Süd' })).toContainText('4,10');
	await expect(card.locator('.string').filter({ hasText: 'Süd' })).toContainText(/\d,\d\s?kWh/, {
		timeout: 30_000
	});
	await expect(page.getByRole('heading', { name: 'Leistung' })).toBeVisible();
	await expect(page.locator('canvas').first()).toBeVisible();

	// das Jahr: 12 Balkengruppen, Jänner bis Dezember
	await page.getByRole('button', { name: 'Jahr' }).click();
	await expect(page).toHaveURL(/zeitraum=jahr&tag=2026-10-09/);
	await expect(page.getByRole('heading', { name: '2026', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Je Monat' })).toBeVisible();
	const months = page.getByRole('table', { name: 'Ertrag je Monat' }).locator('tbody tr');
	await expect(months).toHaveCount(12);
	await expect(months.first()).toContainText('Jänner 2026');

	// bei 360 px ohne seitliches Scrollen, hell und dunkel
	await page.setViewportSize({ width: 360, height: 740 });
	for (const scheme of ['light', 'dark'] as const) {
		await page.emulateMedia({ colorScheme: scheme });
		await page.waitForTimeout(300); // das Diagramm passt sich der Breite an
		expect(await noHorizontalScroll(page), scheme).toBeLessThanOrEqual(0);
	}

	// löschen: erst den String, dann die Anlage
	await card.getByRole('button', { name: 'Süd ändern' }).click();
	await page
		.getByRole('dialog', { name: 'String ändern' })
		.getByRole('button', { name: 'Löschen' })
		.click();
	await page.getByRole('button', { name: 'Endgültig löschen' }).click();
	await expect(page.getByText('„Süd“ gelöscht.')).toBeVisible();
	await card.getByRole('button', { name: 'Huawei ändern' }).click();
	await page
		.getByRole('dialog', { name: 'Anlage ändern' })
		.getByRole('button', { name: 'Löschen' })
		.click();
	await page.getByRole('button', { name: 'Endgültig löschen' }).click();
	await expect(page.getByText('„Huawei“ gelöscht.')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Huawei', level: 2 })).toHaveCount(0);
});

test('PV-Anlagen sind für Lesende sichtbar, ohne Knöpfe zum Ändern', async ({ page }) => {
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/pv?zeitraum=monat&tag=2026-10-09');
	await expect(page.getByRole('heading', { name: 'Oktober 2026' })).toBeVisible();
	await expect(page.getByTestId('pv-total')).toContainText('PV gesamt');
	await expect(page.getByRole('button', { name: /hinzufügen/ })).toHaveCount(0);
});
