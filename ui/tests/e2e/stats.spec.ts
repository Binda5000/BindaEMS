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

test('Statistik hat bei 360 px keine waagrechte Scrollleiste', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 740 });
	await login(page, 'gast');
	await page.clock.setFixedTime(T_DEMO);
	await page.goto('/statistik?zeitraum=monat&datum=2026-10-09');
	await expect(page.getByRole('cell', { name: 'Grundgebühren' })).toBeVisible();
	await expect(page.locator('canvas').first()).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
});
