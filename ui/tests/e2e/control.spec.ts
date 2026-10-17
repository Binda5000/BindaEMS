// Bedienung: Lademodus (Laden), Live-Dialog (System) und Entscheidungen. Das Demo-Backend ist ein
// gemeinsamer Zustand: Der „Sofort“-Test stellt den Tesla am Ende auf „Min+PV“ zurück (auch bei
// einem Fehlschlag), damit keine andere Datei von seiner Änderung abhängt.
import { expect, test, type Page } from '@playwright/test';
import { login } from './helpers';

/** Lademodus des Tesla auf der Seite „Laden“ (Auswahl der Karte „Tesla Model 3“) */
function teslaCard(page: Page) {
	return page
		.locator('section')
		.filter({ has: page.getByRole('heading', { name: 'Tesla Model 3', exact: true }) });
}

function modeSelect(page: Page) {
	return teslaCard(page).getByRole('combobox', { name: 'Lademodus' });
}

async function setMode(page: Page, label: string): Promise<void> {
	await page.goto('/laden');
	await expect(modeSelect(page)).toBeEnabled();
	const reply = page.waitForResponse((response) =>
		response.url().endsWith('/api/control/vehicles/tesla/mode')
	);
	await modeSelect(page).selectOption({ label });
	expect((await reply).status()).toBe(200);
}

test('Bedienen stellt den Tesla auf Sofort', async ({ page, browser }) => {
	await login(page, 'bedienen');
	await page.goto('/laden');
	await expect(page.getByRole('heading', { level: 1, name: 'Laden' })).toBeVisible();
	await expect(modeSelect(page)).toHaveValue('min_pv');
	try {
		await setMode(page, 'Sofort');

		// nach dem Neuladen der Seite steht „Sofort“ (die Auswahl folgt der Absicht; die Anzeige „Modus“
		// kommt aus Health und ändert sich erst mit einem Zyklus des core, den die Demo nicht hat)
		await page.reload();
		await expect(modeSelect(page)).toHaveValue('now');

		// das Änderungsprotokoll nennt die Änderung (als admin, denn „Bedienen“ sieht es nicht)
		const context = await browser.newContext();
		try {
			const adminPage = await context.newPage();
			await login(adminPage, 'admin');
			await adminPage.goto('/protokoll');
			const row = adminPage
				.getByRole('row')
				.filter({ hasText: 'Lademodus geändert' })
				.filter({ hasText: 'bedienen (UI)' });
			await expect(row.first()).toBeVisible();
			await expect(row.first()).toContainText('tesla');
			await expect(row.first()).toContainText('from: min_pv');
			await expect(row.first()).toContainText('to: now');
			await expect(row.first()).toContainText('result: accepted');
		} finally {
			await context.close();
		}
	} finally {
		await setMode(page, 'Min+PV');
		await page.reload();
		await expect(modeSelect(page)).toHaveValue('min_pv');
	}
});

/** Karte „Akku-Unterstützung“ auf der Seite „Laden“ */
function supportCard(page: Page) {
	return page
		.locator('section')
		.filter({ has: page.getByRole('heading', { name: 'Akku-Unterstützung', exact: true }) });
}

async function setSupportAllowed(page: Page, allowed: boolean): Promise<void> {
	const box = supportCard(page).getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' });
	await expect(box).toBeEnabled();
	const reply = page.waitForResponse((response) => response.url().endsWith('/api/control/support'));
	await (allowed ? box.check() : box.uncheck());
	expect((await reply).status()).toBe(200);
}

test('Bedienen sieht die Mehrkosten des Boosts, Lesende nicht', async ({ page, browser }) => {
	await login(page, 'bedienen');
	await page.goto('/laden');
	// Demo: Leistungspreis 60 €/kW/Jahr netto, Spitze 1,78 kW (Mindestleistung 2 kW), Tesla 11,04 kW
	await expect(
		teslaCard(page).getByText(
			'Boost kann bis zu 66,24 € Leistungspreis im Monat kosten (11,0 kW mehr Spitze).'
		)
	).toBeVisible();

	const context = await browser.newContext();
	try {
		const guestPage = await context.newPage();
		await login(guestPage, 'gast');
		await guestPage.goto('/laden');
		await expect(teslaCard(guestPage)).toBeVisible();
		await expect(guestPage.getByText(/Leistungspreis im Monat/)).toHaveCount(0);
	} finally {
		await context.close();
	}
});

test('Bedienen schaltet die Akku-Unterstützung aus', async ({ page, browser }) => {
	await login(page, 'bedienen');
	await page.goto('/laden');
	const box = supportCard(page).getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' });
	await expect(box).toBeChecked();
	await expect(supportCard(page).getByText(/^Wirksam ab 70\s%/)).toBeVisible();
	try {
		await setSupportAllowed(page, false);

		// nach dem Neuladen der Seite steht die Unterstützung auf „aus“
		await page.reload();
		await expect(box).not.toBeChecked();

		// Lesende sehen den Stand ohne Bedienung
		const context = await browser.newContext();
		try {
			const guestPage = await context.newPage();
			await login(guestPage, 'gast');
			await guestPage.goto('/laden');
			const card = supportCard(guestPage);
			await expect(card.getByText('nein')).toBeVisible();
			await expect(card.getByRole('checkbox')).toHaveCount(0);
		} finally {
			await context.close();
		}

		// das Änderungsprotokoll nennt die Änderung (als admin)
		const adminContext = await browser.newContext();
		try {
			const adminPage = await adminContext.newPage();
			await login(adminPage, 'admin');
			await adminPage.goto('/protokoll');
			const row = adminPage
				.getByRole('row')
				.filter({ hasText: 'Akku-Unterstützung geändert' })
				.filter({ hasText: 'bedienen (UI)' });
			await expect(row.first()).toBeVisible();
			await expect(row.first()).toContainText('to: gesperrt');
		} finally {
			await adminContext.close();
		}
	} finally {
		await setSupportAllowed(page, true);
		await page.reload();
		await expect(box).toBeChecked();
	}
});

test('Admin öffnet den Live-Dialog und bricht ab', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/system');
	const rows = page
		.getByRole('table')
		.filter({ has: page.getByRole('columnheader', { name: 'live erlaubt' }) })
		.locator('tbody tr');
	await expect(rows.first()).toBeVisible();
	const before = await rows.allInnerTexts();
	expect(before.length).toBeGreaterThan(0);
	for (const row of before) expect(row).toContain('Dry-Run');

	await page.getByRole('button', { name: 'Live schalten…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Live schalten' });
	await expect(dialog).toBeVisible();
	// in der Demo-Welt ist kein Aktor live erlaubt: nichts ist wählbar
	await expect(dialog.getByText('live erlaubt: nein').first()).toBeVisible();
	await expect(dialog.getByText('Watchdog scharf').first()).toBeVisible();
	await expect(dialog.getByText('Regelbetrieb an').first()).toBeVisible();
	await expect(dialog.getByText('nicht gesperrt').first()).toBeVisible();
	await expect(dialog.getByText('EVCS und Tesla gehen gemeinsam live.')).toBeVisible();
	await expect(dialog.getByRole('checkbox', { name: /Victron/ })).toBeDisabled();
	await expect(dialog.getByRole('button', { name: 'Live schalten' })).toBeDisabled();

	await dialog.getByRole('button', { name: 'Abbrechen' }).click();
	await expect(dialog).toBeHidden();

	// die Aktoren sind unverändert im Dry-Run
	await page.reload();
	await expect(rows.first()).toBeVisible();
	expect(await rows.allInnerTexts()).toEqual(before);
});

test('Entscheidungen filtern', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/entscheidungen');
	// die Entscheidungen der Demo stammen vom Tag der Demo-Uhr, nicht von heute
	await page.getByLabel('Zeitraum').selectOption({ label: 'Eigenes Datum' });
	await page.getByLabel('Datum', { exact: true }).fill('2026-10-09');
	const rows = page.locator('tbody tr');
	await expect(rows).toHaveCount(6); // alle Entscheidungen der Demo-Welt

	await page.getByLabel('Ergebnis').selectOption({ label: 'von außen geändert' });
	await expect(rows).toHaveCount(1);
	await expect(rows.first()).toContainText('EVCS „evcs“ (Änderung von außen)');
	await expect(rows.first()).toContainText('von außen geändert: erwartet 8 A');
	await expect(rows.first().getByRole('cell').last()).toHaveText('von außen geändert');
});
