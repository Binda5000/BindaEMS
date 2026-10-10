import { expect, test } from '@playwright/test';
import { DEMO_PASSWORD, DEMO_TOTP_SECRET, T_DEMO, login, totp } from './helpers';

test('gast meldet sich an und sieht die Übersicht mit Live-Werten', async ({ page }) => {
	await page.goto('/');
	await expect(page).toHaveURL(/\/login\?next=%2F$/);
	await login(page, 'gast');
	await expect(page.getByRole('heading', { level: 1, name: 'Übersicht' })).toBeVisible();
	await expect(page.getByTestId('flow-pv')).toContainText('kW');
	await expect(page.getByText('live', { exact: true })).toBeVisible();
	await expect(page.getByRole('navigation').getByRole('link', { name: 'Benutzer' })).toHaveCount(0);
});

test('Admin mit TOTP braucht den Bestätigungscode', async ({ page }, testInfo) => {
	await page.goto('/login');
	await page.getByLabel('Benutzername').fill('sicher');
	await page.getByLabel('Passwort').fill(DEMO_PASSWORD);
	await page.getByRole('button', { name: 'Anmelden' }).click();
	// jeder Code gilt nur einmal; eine Wiederholung nimmt den nächsten Zeitschritt
	const at = new Date(T_DEMO.getTime() + 30_000 * testInfo.retry);
	await page.getByLabel('Bestätigungscode').fill(totp(DEMO_TOTP_SECRET, at));
	await page.getByRole('button', { name: 'Anmelden' }).click();
	await expect(page.getByRole('navigation').getByRole('link', { name: 'Protokoll' })).toBeVisible();
});

test('Lesende erhalten auf Admin-Seiten „Keine Berechtigung“', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/benutzer');
	await expect(page.getByText('Keine Berechtigung')).toBeVisible();
});

test('ein falsches Passwort bleibt auf der Anmeldung', async ({ page }) => {
	await page.goto('/login');
	await page.getByLabel('Benutzername').fill('gast');
	await page.getByLabel('Passwort').fill('falsch-falsch');
	await page.getByRole('button', { name: 'Anmelden' }).click();
	await expect(page.getByText('Benutzername oder Passwort falsch')).toBeVisible();
	await expect(page).toHaveURL(/\/login/);
});

test('beendete Sitzung führt mit Hinweis zur Anmeldung und danach zurück', async ({ browser }) => {
	const viewer = await browser.newPage();
	const admin = await browser.newPage();
	await login(viewer, 'gast');
	await viewer.goto('/verlauf');
	await expect(viewer.getByRole('heading', { level: 1, name: 'Verlauf' })).toBeVisible();
	await login(admin, 'admin');
	await admin.goto('/benutzer');
	const role = admin.getByLabel('Rolle von gast');
	try {
		// ändert ein Admin die Rolle, sind die Sitzungen von gast beendet
		await role.selectOption('operator');
		await expect(admin.getByText('Rolle von „gast“: Bedienen.')).toBeVisible();
		await viewer.getByLabel('Erster Tag').fill('2026-10-08'); // nächste Anfrage von gast
		await expect(viewer).toHaveURL(/\/login\?next=%2Fverlauf.*abgelaufen=1/);
		await expect(viewer.getByText('Sitzung abgelaufen – bitte neu anmelden.')).toBeVisible();
	} finally {
		await role.selectOption('viewer'); // für die anderen Abläufe
		await expect(admin.getByText('Rolle von „gast“: Lesen.')).toBeVisible();
	}
	await viewer.getByLabel('Benutzername').fill('gast');
	await viewer.getByLabel('Passwort').fill(DEMO_PASSWORD);
	await viewer.getByRole('button', { name: 'Anmelden' }).click();
	await expect(viewer).toHaveURL(/\/verlauf$/);
	await viewer.close();
	await admin.close();
});
