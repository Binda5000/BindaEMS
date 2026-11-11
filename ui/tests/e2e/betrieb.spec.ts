import { expect, test } from '@playwright/test';
import { login, watchPage } from './helpers';

test('Betrieb zeigt Betriebscheck, Auffälligkeiten und Schalter der Demo-Welt', async ({
	page
}) => {
	const watch = watchPage(page);
	await login(page, 'gast');
	await page.goto('/system');
	await page.getByRole('link', { name: /^Betriebscheck: / }).click();
	await expect(page).toHaveURL(/\/betrieb$/);
	await expect(page.getByRole('heading', { level: 1, name: 'Betrieb' })).toBeVisible();

	const check = page
		.locator('section')
		.filter({ has: page.getByRole('heading', { level: 2, name: 'Betriebscheck' }) });
	const findings = page
		.locator('section')
		.filter({ has: page.getByRole('heading', { level: 2, name: 'Auffälligkeiten' }) });
	const switches = page
		.locator('section')
		.filter({ has: page.getByRole('heading', { level: 2, name: 'Schalter' }) });
	await expect(check.getByText(/Prüfungen in Ordnung/)).toBeVisible();
	await expect(
		findings.getByRole('heading', { level: 3, name: 'Tagesbericht über gestern' })
	).toBeVisible();
	await expect(findings.getByText('keine').first()).toBeVisible();
	await expect(switches.getByText('geändert').first()).toBeVisible();
	await expect(switches.getByText('seit Einrichtung').first()).toBeVisible();

	// grüne Prüfungen klappen auf
	await check.getByText(/Prüfungen in Ordnung/).click();
	await expect(check.getByText('core verbunden')).toBeVisible();

	// der Link der Auffälligkeit „Netzladen ohne Plan“ setzt die Filter der Entscheidungen
	const grid = findings.getByRole('listitem').filter({ hasText: 'aus dem Netz' }).first();
	await grid.getByRole('link', { name: 'Entscheidungen öffnen' }).click();
	await expect(page).toHaveURL(/\/entscheidungen\?range=2026-10-08&actuator=victron$/);
	await expect(page.getByRole('combobox', { name: /^Zeitraum/ })).toHaveValue('date');
	await expect(page.getByRole('textbox', { name: 'Datum' })).toHaveValue('2026-10-08');
	await expect(page.getByRole('combobox', { name: /^Aktor/ })).toHaveValue('victron');
	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});
