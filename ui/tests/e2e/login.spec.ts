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
