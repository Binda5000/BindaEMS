import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import TariffEditorHost from '$lib/testing/TariffEditorHost.svelte';
import { draftFrom, type TariffDraft } from './model';

const GRID = 'Netznutzungsentgelt Arbeitspreis (Netzebene 7)';

function renderEditor(errors: Record<string, string> = {}) {
	const probe: { draft: TariffDraft | null; structure: number } = { draft: null, structure: 0 };
	render(TariffEditorHost, {
		initial: draftFrom(settingsCurrent().settings).tariff,
		errors,
		probe
	});
	const draft = () => {
		if (!probe.draft) throw new Error('Entwurf fehlt');
		return probe.draft;
	};
	return { draft, probe };
}

const press = (scope: HTMLElement, name: string) =>
	fireEvent.click(within(scope).getByRole('button', { name }));

it('adds and removes a component', async () => {
	const { draft, probe } = renderEditor();
	await fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'fixed_per_month' } });
	await press(document.body, 'Bestandteil hinzufügen');
	expect(draft().components.at(-1)).toMatchObject({
		kind: 'fixed_per_month',
		id: 'neue_gebuehr',
		name: 'Neue Gebühr'
	});
	expect(probe.structure).toBe(1);
	await fireEvent.click(screen.getAllByRole('button', { name: 'Bestandteil entfernen' }).at(-1)!);
	expect(draft().components.some((c) => c.id === 'neue_gebuehr')).toBe(false);
	expect(probe.structure).toBe(2);
});

it('names a new component after its kind and keeps ids unique', async () => {
	const { draft } = renderEditor();
	for (const [kind, name] of [
		['energy_per_kwh', 'Neue Energie'],
		['power_per_kw_year', 'Neuer Leistungspreis'],
		['feed_in_per_kwh', 'Neue Einspeisung'],
		['feed_in_per_kwh', 'Neue Einspeisung']
	]) {
		await fireEvent.change(screen.getByLabelText('Art'), { target: { value: kind } });
		await press(document.body, 'Bestandteil hinzufügen');
		expect(draft().components.at(-1)).toMatchObject({ kind, name });
	}
	expect(
		draft()
			.components.slice(-2)
			.map((c) => c.id)
	).toEqual(['neue_einspeisung', 'neue_einspeisung_2']);
});

it('never offers to remove the spot price', () => {
	renderEditor();
	const spot = screen.getByRole('group', { name: 'Börsenpreis' });
	expect(within(spot).queryByRole('button', { name: 'Bestandteil entfernen' })).toBeNull();
	expect(within(spot).getByText('kommt aus der Preisquelle')).toBeTruthy();
	const markup = screen.getByRole('group', { name: 'Lieferantenaufschlag' });
	expect(within(markup).getByRole('button', { name: 'Bestandteil entfernen' })).toBeTruthy();
});

it('shows tier and minimum only for the power price', () => {
	renderEditor();
	const power = screen.getByRole('group', { name: 'Leistungspreis (ElWG)' });
	expect(within(power).getByLabelText('Stufe (kW)')).toBeTruthy();
	expect(within(power).getByLabelText('Mindestverrechnung (kW)')).toBeTruthy();
	expect(within(power).getByLabelText('bis zur Stufe (€/kW/Jahr netto)')).toBeTruthy();
	expect(within(power).getByLabelText('über der Stufe (€/kW/Jahr netto)')).toBeTruthy();
	expect(within(power).queryByText('Zeitfenster')).toBeNull();
	const fee = screen.getByRole('group', { name: 'Grundgebühr Netz' });
	expect(within(fee).queryByLabelText('Stufe (kW)')).toBeNull();
	expect(within(fee).getByLabelText('Wert netto')).toBeTruthy();
});

it('shows server errors at the window field', () => {
	renderEditor({
		'tariff.components.2.windows.0.end': 'Ende muss nach dem Beginn liegen (00:00 = Tagesende)'
	});
	expect(screen.getByText('Ende muss nach dem Beginn liegen (00:00 = Tagesende)')).toBeTruthy();
});

it('shows rule errors of the window, the component and the tariff', () => {
	renderEditor({
		'tariff.components.2.windows.0': 'Zeitfenster braucht genau eines von factor oder value_ct',
		'tariff.components.6': 'valid_from liegt nach valid_until',
		tariff: 'Leistungspreise dürfen sich zeitlich nicht überschneiden'
	});
	const grid = screen.getByRole('group', { name: GRID });
	const window = within(grid).getByRole('group', { name: 'Zeitfenster 1' });
	expect(
		within(window).getByText('Zeitfenster braucht genau eines von factor oder value_ct')
	).toBeTruthy();
	const power = screen.getByRole('group', { name: 'Leistungspreis (ElWG)' });
	expect(within(power).getByText('valid_from liegt nach valid_until')).toBeTruthy();
	expect(screen.getByText('Leistungspreise dürfen sich zeitlich nicht überschneiden')).toBeTruthy();
});

it('edits the SNAP window: months, weekdays, times and factor', async () => {
	const { draft } = renderEditor();
	const grid = screen.getByRole('group', { name: GRID });
	const window = within(grid).getByRole('group', { name: 'Zeitfenster 1' });
	const may = within(window).getByRole('button', { name: 'Mai' });
	expect(may).toHaveAttribute('aria-pressed', 'true');
	await fireEvent.click(may);
	expect(draft().components[2].windows[0].months).toEqual([4, 6, 7, 8, 9]);
	await fireEvent.click(within(window).getByRole('button', { name: 'Jän' }));
	expect(draft().components[2].windows[0].months).toEqual([1, 4, 6, 7, 8, 9]);
	await fireEvent.click(within(window).getByRole('button', { name: 'So' }));
	expect(draft().components[2].windows[0].weekdays).toEqual([0, 1, 2, 3, 4, 5]);
	await fireEvent.change(within(window).getByLabelText('von'), { target: { value: '09:15' } });
	await fireEvent.change(within(window).getByLabelText('bis'), { target: { value: '00:00' } });
	expect(within(window).getByLabelText('bis')).toHaveDisplayValue('24:00');
	await fireEvent.click(within(window).getByLabelText('fester Wert (ct/kWh netto)'));
	await fireEvent.input(within(window).getByLabelText('Wert'), { target: { value: '0,75' } });
	expect(draft().components[2].windows[0]).toMatchObject({
		start: '09:15',
		end: '00:00',
		mode: 'value',
		amount: '0,75'
	});
});

it('adds and removes a window', async () => {
	const { draft, probe } = renderEditor();
	const grid = screen.getByRole('group', { name: GRID });
	await press(grid, 'Zeitfenster hinzufügen');
	expect(draft().components[2].windows).toHaveLength(2);
	expect(draft().components[2].windows[1]).toMatchObject({ mode: 'factor', amount: '1' });
	expect(within(grid).getByRole('group', { name: 'Zeitfenster 2' })).toBeTruthy();
	await press(within(grid).getByRole('group', { name: 'Zeitfenster 1' }), 'Zeitfenster entfernen');
	expect(draft().components[2].windows).toHaveLength(1);
	expect(draft().components[2].windows[0].amount).toBe('1');
	expect(probe.structure).toBe(2);
});

it('edits validity, VAT flag and tariff-wide values', async () => {
	const { draft } = renderEditor();
	const power = screen.getByRole('group', { name: 'Leistungspreis (ElWG)' });
	await fireEvent.input(within(power).getByLabelText('gültig bis'), {
		target: { value: '2027-12-31' }
	});
	await fireEvent.click(within(power).getByLabelText('mit USt.'));
	await fireEvent.input(screen.getByLabelText('USt. (%)'), { target: { value: '19' } });
	await fireEvent.input(screen.getByLabelText('Fixtarif zum Vergleich (ct/kWh brutto)'), {
		target: { value: '31,5' }
	});
	expect(draft().components[6]).toMatchObject({
		validFrom: '2027-01-01',
		validUntil: '2027-12-31',
		vat: false
	});
	expect(draft()).toMatchObject({ vatPct: '19', fixedPriceCt: '31,5' });
});
