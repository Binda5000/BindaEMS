import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { login } from './helpers';

/** Feld „Wert netto“ des Bestandteils im geöffneten Editor */
const valueOf = (page: Page, component: string) =>
	page.getByRole('group', { name: component, exact: true }).getByLabel('Wert netto');

const GRID = 'Netznutzungsentgelt Arbeitspreis (Netzebene 7)';

test('Admin ändert das Netzverlustentgelt und bewertet neu', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	// der Demo-Zustand ist geteilt: einen Wert wählen, der vom aktuellen abweicht
	const loss = valueOf(page, 'Netzverlustentgelt');
	await loss.fill((await loss.inputValue()) === '1,23' ? '1,24' : '1,23');
	await page.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
	await page.getByRole('button', { name: 'Neu bewerten' }).click();
	await expect(page.getByText(/\d+ Viertelstunden neu bewertet\./)).toBeVisible();
});

test('Konflikt bei gleichzeitiger Änderung behält die Eingaben', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await valueOf(page, 'Elektrizitätsabgabe').fill('0,1');
	// ein zweiter Admin speichert dazwischen
	const current = await (await page.request.get('/api/settings')).json();
	const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'bindaems_csrf');
	const old = current.settings.feed_in.monthly_ct['2026-08'];
	const monthly = { ...current.settings.feed_in.monthly_ct, '2026-08': old === 6.5 ? 6.6 : 6.5 };
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
	await expect(valueOf(page, 'Elektrizitätsabgabe')).toHaveValue('0,1');
});

test('admin edits the SNAP window and saves', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	// der Demo-Zustand ist geteilt: vom aktuellen Wert aus ändern und am Ende zurücksetzen
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	const factor = page
		.getByRole('group', { name: GRID, exact: true })
		.getByRole('group', { name: 'Zeitfenster 1' })
		.getByRole('textbox', { name: 'Wert', exact: true });
	const original = await factor.inputValue();
	const changed = original === '0,75' ? '0,8' : '0,75';
	try {
		await factor.fill(changed);
		await page.getByRole('button', { name: 'Speichern' }).click();
		await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
		// Leseansicht und das Formular zum Neubewerten
		await expect(page.getByText(`Apr–Sep, Mo–So, 10:00–16:00: × ${changed}`)).toBeVisible();
		await expect(page.getByRole('button', { name: 'Neu bewerten' })).toBeVisible();
		await page.reload();
		await expect(page.getByText(`Apr–Sep, Mo–So, 10:00–16:00: × ${changed}`)).toBeVisible();
	} finally {
		await page.goto('/einstellungen');
		await page.getByRole('button', { name: 'Bearbeiten' }).click();
		await factor.fill(original);
		await page.getByRole('button', { name: 'Speichern' }).click();
		await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
	}
});

test('Tarif-Editor bei 360 px: ohne waagrechtes Scrollen, Bedienelemente mindestens 44 px', async ({
	page
}) => {
	await page.setViewportSize({ width: 360, height: 740 });
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await page.getByRole('button', { name: 'Zeitfenster hinzufügen' }).first().click();
	await expect(page.getByRole('button', { name: 'Bestandteil hinzufügen' })).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
	const small = await page.evaluate(() => {
		const selector = [
			'form.editor button',
			'form.editor select',
			'form.editor input:not([type=checkbox]):not([type=radio])',
			'form.editor label:has(> input[type=checkbox])',
			'form.editor label:has(> input[type=radio])'
		].join(', ');
		const found: string[] = [];
		for (const element of document.querySelectorAll(selector)) {
			const box = element.getBoundingClientRect();
			if (box.width === 0 || box.height === 0) continue;
			if (box.width < 44 || box.height < 44) {
				const name = (element.getAttribute('aria-label') ?? element.textContent ?? '').trim();
				found.push(
					`${element.tagName} „${name.slice(0, 30)}“ ${Math.round(box.width)}×${Math.round(box.height)}`
				);
			}
		}
		return found;
	});
	expect(small).toEqual([]);
});

test('admin schaltet Netzladen in den Einstellungen der Planung', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	const view = page.getByRole('heading', { name: 'Planung' }).locator('..');
	await expect(view.getByText('Marge')).toBeVisible();
	// der Demo-Zustand ist geteilt: vom aktuellen Wert aus ändern und am Ende zurücksetzen
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	const box = page.getByRole('checkbox', { name: 'Netzladen erlaubt' });
	const original = await box.isChecked();
	try {
		await box.setChecked(!original);
		await page.getByLabel('Marge (ct/kWh)').fill('2,5');
		await page.getByRole('button', { name: 'Speichern' }).click();
		await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
		await expect(view.getByText('2,5 ct/kWh').first()).toBeVisible();
	} finally {
		await page.goto('/einstellungen');
		await page.getByRole('button', { name: 'Bearbeiten' }).click();
		await box.setChecked(original);
		await page.getByLabel('Marge (ct/kWh)').fill('2');
		await page.getByRole('button', { name: 'Speichern' }).click();
		await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
	}
});

test('Einstellungen der Planung melden ungültige Zahlen am Feld', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await page.getByLabel('Roundtrip-Wirkungsgrad (%)').fill('101');
	await page.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText('Zahl von 50 bis 100')).toBeVisible();
	await expect(page.getByLabel('Roundtrip-Wirkungsgrad (%)')).toBeFocused();
});

test('admin wählt einen Sensor für das Lastmodell und speichert ihn', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	const view = page.getByRole('heading', { name: 'Lastmodell' }).locator('..');
	await expect(view.getByText('Zusätzliche Sensoren aus Home Assistant')).toBeVisible();
	// der Demo-Zustand ist geteilt: nach dem Test wieder abwählen
	const choose = async (checked: boolean) => {
		await page.goto('/einstellungen');
		await page.getByRole('button', { name: 'Bearbeiten' }).click();
		await page.getByRole('checkbox', { name: 'binary_sensor.anwesenheit' }).setChecked(checked);
		await page.getByRole('button', { name: 'Speichern' }).click();
		await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
	};
	await choose(true);
	try {
		await expect(view.getByText('binary_sensor.anwesenheit')).toBeVisible();
	} finally {
		await choose(false);
	}
	await expect(view.getByText('binary_sensor.anwesenheit')).toHaveCount(0);
});
