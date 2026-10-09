import { expect, test } from '@playwright/test';
import { login } from './helpers';

const LOSS = 'Netzverlustentgelt (ct/kWh netto)';
const TAX = 'Elektrizitätsabgabe (ct/kWh netto)';

test('Admin ändert das Netzverlustentgelt und bewertet neu', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await page.getByLabel(LOSS).fill('1,23');
	await page.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
	await page.getByRole('button', { name: 'Neu bewerten' }).click();
	await expect(page.getByText(/\d+ Viertelstunden neu bewertet\./)).toBeVisible();
});

test('Konflikt bei gleichzeitiger Änderung behält die Eingaben', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await page.getByLabel(TAX).fill('0,1');
	// ein zweiter Admin speichert dazwischen
	const current = await (await page.request.get('/api/settings')).json();
	const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'bindaems_csrf');
	const monthly = { ...current.settings.feed_in.monthly_ct, '2026-08': 6.5 };
	const response = await page.request.put('/api/settings', {
		data: {
			base_version: current.version,
			settings: { ...current.settings, feed_in: { monthly_ct: monthly } }
		},
		headers: { 'X-CSRF-Token': csrf?.value ?? '' }
	});
	expect(response.ok()).toBe(true);
	await page.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText(/inzwischen geändert/)).toBeVisible();
	await expect(page.getByLabel(TAX)).toHaveValue('0,1');
});
