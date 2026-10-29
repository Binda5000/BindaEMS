import { expect, test } from '@playwright/test';
import { T_DEMO, login } from './helpers';

test('Statistik wechselt den Zeitraum und zeigt Diagramm und Kosten', async ({ page }) => {
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO); // „heute“ ist der 09.10.2026, wie in der Demo-Welt
	await page.goto('/statistik');
	await expect(page.getByRole('heading', { name: 'Fr., 09.10.2026' })).toBeVisible();

	await page.getByRole('button', { name: 'Monat' }).click();
	await expect(page.getByRole('heading', { name: 'Oktober 2026' })).toBeVisible();
	await expect(page).toHaveURL(/zeitraum=monat&datum=2026-10-09/);
	await expect(page.getByTestId('tile')).toHaveCount(5);
	// Euro überall „0,52 €“: Zahl, geschütztes Leerzeichen, Zeichen
	await expect(page.getByTestId('tile').filter({ hasText: 'Kosten' })).toContainText(
		new RegExp('\\d,\\d\\d\\u00a0€')
	);
	await expect(page.getByRole('table', { name: 'Kosten' })).not.toContainText(/€\s?\d/);
	await expect(page.locator('canvas').first()).toBeVisible();
	await expect(page.getByRole('cell', { name: 'Grundgebühren' })).toBeVisible();
	await expect(page.getByRole('cell', { name: 'Leistungspreis' })).toBeVisible();

	// zurück in den September, die Woche und der Tag haben keinen Leistungspreis
	await page.getByRole('button', { name: 'Zurück' }).click();
	await expect(page.getByRole('heading', { name: 'September 2026' })).toBeVisible();
	await page.getByRole('button', { name: 'Woche' }).click();
	await expect(page.getByRole('heading', { name: 'KW 37 · 07.09.–13.09.2026' })).toBeVisible();
	await expect(page.getByRole('cell', { name: 'Grundgebühren' })).toBeVisible();
	await expect(page.getByRole('cell', { name: 'Leistungspreis' })).toHaveCount(0);

	await page.getByRole('button', { name: 'Heute' }).click();
	await expect(page.getByRole('heading', { name: 'KW 41 · 05.10.–11.10.2026' })).toBeVisible();
});

test('Statistik zeigt im Monat Autos, Ladesessions, Verbraucher, Monatsspitzen und Abgleich', async ({
	page
}) => {
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=monat&datum=2026-10-09');
	for (const title of [
		'Autos',
		'Ladesessions',
		'Verbraucher',
		'Monatsspitzen',
		'Abgleich mit den Zählern'
	]) {
		await expect(page.getByRole('heading', { name: title, level: 2 })).toBeVisible();
	}
	await expect(page.getByRole('cell', { name: 'Akku → Autos' })).toBeVisible();
	await expect(page.getByRole('table', { name: 'Ladesessions' })).toContainText('läuft');
	await expect(page.getByText(/^Mehr als 1 % Abweichung am /)).toBeVisible();

	// in der Woche gibt es keine Monatsspitzen
	await page.getByRole('button', { name: 'Woche' }).click();
	await expect(page.getByRole('heading', { name: /^KW 41/ })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Monatsspitzen', level: 2 })).toHaveCount(0);
});

test('Statistik filtert die Ladesessions nach Auto und zeigt den Verlauf', async ({ page }) => {
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=monat&datum=2026-10-09');
	const card = page
		.locator('section')
		.filter({ has: page.getByRole('heading', { name: 'Laden je Auto', level: 2 }) });
	await expect(card.getByRole('heading', { name: 'Laden je Auto', level: 2 })).toBeVisible();
	await expect(card.locator('canvas')).toBeVisible();

	// die Auswahl der Autos, nicht der gleichnamige Chip der Legende unter dem Diagramm
	await card.getByLabel('Auto wählen').getByRole('button', { name: 'Tesla Model 3' }).click();
	await expect(page).toHaveURL(/auto=tesla/);
	const rows = page.getByRole('table', { name: 'Ladesessions' }).locator('tbody tr');
	// erst die gefilterte Liste abwarten: solange stehen noch die Zeilen aller Autos da
	await expect(rows.filter({ hasNotText: 'Tesla Model 3' })).toHaveCount(0);
	await expect(rows.first()).toBeVisible();
	await expect(page.locator('.results')).toHaveAttribute('aria-busy', 'false');

	// Blättern behält das Auto
	await page.getByRole('button', { name: 'Zurück' }).click();
	await expect(page.getByRole('heading', { name: 'September 2026' })).toBeVisible();
	await expect(page).toHaveURL(/auto=tesla/);

	await card.getByRole('button', { name: '€', exact: true }).click();
	await expect(card.getByRole('button', { name: '€', exact: true })).toHaveAttribute(
		'aria-pressed',
		'true'
	);

	await card.getByRole('button', { name: 'Alle' }).click();
	await expect(page).not.toHaveURL(/auto=/);
});

test('Statistik speichert die Ladesessions als CSV', async ({ page }) => {
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=monat&datum=2026-10-09');
	const download = page.waitForEvent('download');
	await page.getByRole('button', { name: 'CSV herunterladen' }).click();
	expect((await download).suggestedFilename()).toBe('ladesessions-2026-10-01-2026-10-31.csv');
});

test('Statistik bleibt mit einem langen Schlüssel in der Adresse bei 360 px schmal', async ({
	page
}) => {
	await page.setViewportSize({ width: 360, height: 740 });
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto(`/statistik?zeitraum=monat&datum=2026-10-09&auto=${'x'.repeat(120)}`);
	await expect(page.getByRole('button', { name: 'x'.repeat(120) })).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
});

test('Statistik hat bei 360 px keine waagrechte Scrollleiste', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 740 });
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=monat&datum=2026-10-09');
	await expect(page.getByRole('cell', { name: 'Grundgebühren' })).toBeVisible();
	await expect(page.locator('canvas').first()).toBeVisible();
	// breite Tabellen werden am Handy zu Karten: weder die Seite noch ihr Rahmen scrollt
	const sessions = page.getByRole('table', { name: 'Ladesessions' });
	await expect(sessions).toBeVisible();
	const wide = await sessions.evaluate((table) => {
		const box = table.parentElement as HTMLElement;
		return {
			scrolls: box.scrollWidth > box.clientWidth,
			overflowX: getComputedStyle(box).overflowX
		};
	});
	expect(wide).toEqual({ scrolls: false, overflowX: 'auto' });
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
});

test('Statistik hält Heute und den Titel bei 390 px in einer Zeile', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=woche&datum=2026-10-02');
	const title = page.getByRole('heading', { name: /^KW 40/ });
	await expect(title).toBeVisible();
	const middle = async (locator: typeof title) => {
		const box = await locator.boundingBox();
		return box ? box.y + box.height / 2 : Number.NaN;
	};
	const titleY = await middle(title);
	for (const name of ['Zurück', 'Weiter', 'Heute']) {
		const button = page.getByRole('button', { name });
		expect(Math.abs((await middle(button)) - titleY)).toBeLessThanOrEqual(8);
		expect((await button.boundingBox())?.height).toBeGreaterThanOrEqual(44);
	}
	for (const name of ['Zurück', 'Weiter']) {
		const box = await page.getByRole('button', { name }).boundingBox();
		expect(box?.width).toBeGreaterThanOrEqual(44);
	}
	// zwei große Kacheln oben, drei kleine darunter
	const tiles = page.getByTestId('tile');
	await expect(tiles).toHaveCount(5);
	const tops = await tiles.evaluateAll((items) =>
		items.map((item) => item.getBoundingClientRect().top)
	);
	expect(Math.abs(tops[0] - tops[1])).toBeLessThanOrEqual(1);
	expect(Math.abs(tops[2] - tops[4])).toBeLessThanOrEqual(1);
	expect(tops[2]).toBeGreaterThan(tops[0] + 20);
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
});

test('Statistik zeigt Geldwerte in den Kacheln bei 360 px in einer Zeile', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 740 });
	// lange Beträge einsetzen: die Demo-Werte sind kürzer als „1.234,56 €“
	await page.route('**/api/stats?*', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		body.money = {
			...body.money,
			total_eur: 1234.56,
			savings_no_plant_eur: 1234.56,
			revenue_eur: 1234.56
		};
		await route.fulfill({ response, json: body });
	});
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=jahr&datum=2026-10-09');
	const values = page.getByTestId('tile').locator('.value');
	await expect(values).toHaveCount(5);
	await expect(values.nth(4)).toContainText('1.234,56');
	const metrics = await values.evaluateAll((items) =>
		items.map((item) => {
			const style = getComputedStyle(item);
			const tile = item.closest('[data-testid="tile"]') as HTMLElement;
			return {
				text: item.textContent,
				// weder der Wert noch seine Kachel läuft über
				overflows:
					item.scrollWidth > item.clientWidth ||
					tile.scrollWidth > tile.clientWidth ||
					item.getBoundingClientRect().right > tile.getBoundingClientRect().right,
				lines: item.getBoundingClientRect().height / parseFloat(style.lineHeight)
			};
		})
	);
	for (const metric of metrics) {
		expect(metric.overflows, `${metric.text} läuft über`).toBe(false);
		expect(metric.lines, `${metric.text} bricht um`).toBeLessThan(1.6);
	}
});
