import { expect, test } from '@playwright/test';
import { login, watchPage } from './helpers';

const PAGES = [
	'/',
	'/verlauf',
	'/verbraucher',
	'/system',
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
