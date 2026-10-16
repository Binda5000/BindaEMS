import { expect, test } from '@playwright/test';
import { login, watchPage } from './helpers';

const PAGES = [
	'/',
	'/verlauf',
	'/verbraucher',
	'/laden',
	'/zeitplan',
	'/system',
	'/entscheidungen',
	'/entscheidungen?range=2026-10-09', // Tag der Demo-Uhr: die Tabelle ist gefüllt
	'/einstellungen',
	'/benutzer',
	'/protokoll',
	'/konto'
];

test('alle Seiten bei 360 px ohne CSP-Verstoß, Skriptfehler und waagrechtes Scrollen', async ({
	page
}) => {
	await page.setViewportSize({ width: 360, height: 740 });
	const watch = watchPage(page);
	await login(page, 'admin');
	for (const path of PAGES) {
		await page.goto(path);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		await page.waitForTimeout(500); // Abfragen und Diagramme rendern nach
		const overflow = await page.evaluate(
			() => document.documentElement.scrollWidth - document.documentElement.clientWidth
		);
		expect(overflow, path).toBeLessThanOrEqual(0);
	}
	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});

test('Verlauf zeigt Diagramm und Tagesbilanz', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/verlauf');
	await page.getByLabel('Erster Tag').fill('2026-10-09');
	await page.getByLabel('Letzter Tag').fill('2026-10-09');
	await expect(page.locator('canvas').first()).toBeVisible();
	await expect(page.getByRole('cell', { name: 'Fr., 09.10.' })).toBeVisible();
});

test('Verbraucher zeigen „Sonstiges“, System zeigt Signale', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/verbraucher');
	await expect(page.getByText('Obergeschoss')).toBeVisible();
	await expect(page.getByText('Sonstiges').first()).toBeVisible();
	await page.goto('/system');
	await page.getByLabel('Signal suchen').fill('pv.huawei.power');
	await expect(page.getByRole('cell', { name: 'pv.huawei.power_w' })).toBeVisible();
});

test('Übersicht zeichnet das Diagramm mit Prognosen, System zeigt Lastprognose und Prognosegüte', async ({
	page
}) => {
	await login(page, 'gast');
	const load = page.waitForResponse(
		(response) => response.url().includes('/api/forecast/load') && response.ok()
	);
	await page.goto('/');
	await load;
	const chart = page.locator('section', { hasText: 'Preise und PV heute/morgen' });
	await expect(chart.locator('canvas').first()).toBeVisible();
	await expect(chart.locator('.notice.error')).toHaveCount(0);

	await page.goto('/system');
	const loadCard = page.locator('section', {
		has: page.getByRole('heading', { name: 'Lastprognose' })
	});
	await expect(loadCard.getByText('Datenbasis')).toBeVisible();
	await expect(loadCard.getByText('Übermorgen')).toBeVisible();
	await expect(loadCard.getByText('Viertelstunden aus BindaEMS')).toBeVisible();
	const quality = page.locator('section', {
		has: page.getByRole('heading', { name: 'Prognosegüte' })
	});
	await expect(quality.getByText('letzte 14 Tage')).toBeVisible();
	await expect(quality.getByRole('columnheader', { name: 'MAE relativ' })).toBeVisible();
	await expect(quality.getByRole('cell', { name: 'bis 6 h' }).first()).toBeVisible();
});

/** Bedienelemente unter 44 × 44 px (Global Constraints: Mobil zuerst); Links im Text zählen nicht */
function smallTargets(path: string): string[] {
	const selector = [
		'button',
		'select',
		'textarea',
		'summary',
		'input:not([type=checkbox]):not([type=radio]):not([type=hidden])',
		'label:has(> input[type=checkbox])',
		'label:has(> input[type=radio])'
	].join(', ');
	const small: string[] = [];
	for (const element of document.querySelectorAll(selector)) {
		const box = element.getBoundingClientRect();
		if (box.width === 0 || box.height === 0) continue; // nicht sichtbar (z. B. geschlossener Dialog)
		if (box.width < 44 || box.height < 44) {
			const name = (element.getAttribute('aria-label') ?? element.textContent ?? '').trim();
			small.push(
				`${path} ${element.tagName} „${name.slice(0, 30)}“ ${Math.round(box.width)}×${Math.round(box.height)}`
			);
		}
	}
	return small;
}

test('Bedienelemente sind bei 360 px mindestens 44 × 44 px groß', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 740 });
	await page.goto('/login');
	const small = await page.evaluate(smallTargets, '/login');
	await login(page, 'admin');
	for (const path of PAGES) {
		await page.goto(path);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		await page.waitForTimeout(500);
		small.push(...(await page.evaluate(smallTargets, path)));
	}
	expect(small).toEqual([]);
});
