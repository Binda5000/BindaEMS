// Fahrverhalten auf der Seite „Laden“: Vorschlag, „Übernehmen“ und der Schalter der Automatik. Die
// Demo-Welt (tests/ui_world.py) hat den Tesla mit Pendelprofil und pausierter Automatik, den e-Golf
// mit wenig Daten und ausgeschalteter Automatik. Das Demo-Backend ist ein gemeinsamer Zustand, auf
// dem control.spec.ts den Tesla (Geplant, 60 %, 17:30) voraussetzt: Die Bedienungen hier werden
// abgefangen und nur die gesendete Anfrage geprüft, an der Anlage ändert sich nichts.
import { expect, test, type Page } from '@playwright/test';
import { login } from './helpers';

function carCard(page: Page, name: string) {
	return page.locator('section').filter({ has: page.getByRole('heading', { name, exact: true }) });
}

/** Antwort von PUT …/target: die Absicht, wie sie der Server nach der Änderung meldet */
const TARGET_REPLY = {
	result: 'accepted',
	version: 2,
	details: [],
	intent: {
		version: 2,
		control: true,
		actuators: {},
		vehicles: {
			egolf: {
				mode: 'pv',
				boost_until: null,
				wallbox: null,
				target_soc_pct: 50,
				departure: '08:00',
				departure_on: '2026-10-12'
			}
		},
		priority: null,
		surplus_priority: { kind: 'battery_until_soc', soc_pct: 30.0 },
		guest: { mode: 'pv' },
		plan: null,
		support: { allowed: true, soc_pct: null }
	}
};

test('Bedienen sieht Vorschlag, Zustand und Schalter der Automatik', async ({ page }) => {
	await login(page, 'bedienen');
	await page.goto('/laden');

	const tesla = carCard(page, 'Tesla Model 3');
	await expect(tesla.getByText('Vorschlag: Mo 07:15 · 50 % (Konfidenz hoch)')).toBeVisible();
	const teslaSwitch = tesla.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' });
	await expect(teslaSwitch).toBeChecked();
	await expect(teslaSwitch).toBeEnabled();
	// der Satz des Zustands beschreibt den Schalter (Ruling T13)
	await expect(teslaSwitch).toHaveAccessibleDescription(
		'Automatik pausiert bis Fr 17:30, weil Ziel oder Abfahrt von Hand geändert wurden.'
	);
	await expect(
		tesla.getByText(
			'Automatik pausiert bis Fr 17:30, weil Ziel oder Abfahrt von Hand geändert wurden.'
		)
	).toBeVisible();

	const egolf = carCard(page, 'e-Golf');
	await expect(egolf.getByText('Vorschlag: Mo 08:00 · 50 % (Konfidenz niedrig)')).toBeVisible();
	await expect(
		egolf.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' })
	).not.toBeChecked();
	await expect(egolf.getByText('Automatik aus.')).toBeVisible();
});

test('Übernehmen sendet Ziel, Abfahrt und Abfahrtstag des Vorschlags', async ({ page }) => {
	await login(page, 'bedienen');
	let sent: unknown = null;
	await page.route('**/api/control/vehicles/egolf/target', async (route) => {
		sent = route.request().postDataJSON();
		await route.fulfill({ status: 200, json: TARGET_REPLY });
	});
	await page.goto('/laden');
	const egolf = carCard(page, 'e-Golf');
	await egolf
		.getByRole('group', { name: 'Vorschlag' })
		.getByRole('button', { name: 'Übernehmen' })
		.click();
	await expect
		.poll(() => sent)
		.toEqual({
			target_soc_pct: 50,
			departure: '08:00',
			departure_on: '2026-10-12'
		});
});

test('Der Schalter der Automatik sendet „an“ für den e-Golf', async ({ page }) => {
	await login(page, 'bedienen');
	let sent: unknown = null;
	await page.route('**/api/control/vehicles/egolf/automation', async (route) => {
		sent = route.request().postDataJSON();
		await route.fulfill({
			status: 200,
			json: {
				enabled: true,
				state: 'inactive',
				text: 'Automatik wartet: Das Auto steht nicht auf „Geplant“.',
				paused_until: null,
				last_set_at: null
			}
		});
	});
	await page.goto('/laden');
	const toggle = carCard(page, 'e-Golf').getByRole('switch', {
		name: 'Ziel und Abfahrt automatisch'
	});
	await expect(toggle).toBeEnabled();
	await toggle.check();
	await expect.poll(() => sent).toEqual({ on: true });
});

test('Gast sieht den Vorschlag, aber Schalter gesperrt und ohne Übernehmen', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/laden');
	const egolf = carCard(page, 'e-Golf');
	await expect(egolf.getByText('Vorschlag: Mo 08:00 · 50 % (Konfidenz niedrig)')).toBeVisible();
	await expect(egolf.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' })).toBeDisabled();
	await expect(egolf.getByText('Automatik aus.')).toBeVisible();
	await expect(egolf.getByRole('button', { name: 'Übernehmen' })).toHaveCount(0);
});
