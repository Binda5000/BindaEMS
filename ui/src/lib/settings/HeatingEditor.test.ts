import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import HeatingEditorHost from '$lib/testing/HeatingEditorHost.svelte';
import { applyDraft, draftFrom, type HeatingDraft } from './model';

function renderEditor(errors: Record<string, string> = {}) {
	const probe: { draft: HeatingDraft | null } = { draft: null };
	render(HeatingEditorHost, {
		initial: draftFrom(settingsCurrent().settings).heating,
		errors,
		probe
	});
	return () => {
		if (!probe.draft) throw new Error('Entwurf fehlt');
		return probe.draft;
	};
}

it('zeigt die vier Felder, leer = nicht eingetragen, Leistungszahl 3,0', () => {
	renderEditor();
	expect(screen.getByRole('group', { name: 'Ölheizung' })).toBeInTheDocument();
	expect(screen.getByLabelText('Nennleistung (kW)')).toHaveValue('');
	expect(screen.getByLabelText('Öl je Brennerstunde (l/h)')).toHaveValue('');
	expect(screen.getByLabelText('Ölpreis (€/l, leer = ohne Euro)')).toHaveValue('');
	const cop = screen.getByLabelText('Leistungszahl der Klimaanlage beim Heizen');
	expect(cop).toHaveValue('3');
	expect(cop).toHaveAccessibleDescription('Schätzung: Wärme je kWh Strom.');
});

it('nimmt Eingaben in den Entwurf', async () => {
	const draft = renderEditor();
	await fireEvent.input(screen.getByLabelText('Nennleistung (kW)'), { target: { value: '27' } });
	await fireEvent.input(screen.getByLabelText('Ölpreis (€/l, leer = ohne Euro)'), {
		target: { value: '0,98' }
	});
	expect(draft().burnerKw).toBe('27');
	expect(draft().oilPriceEurL).toBe('0,98');
});

it('zeigt Fehler am Feld', () => {
	renderEditor({ 'heating.burner_kw': 'Zahl von 1 bis 100, z. B. 27' });
	const field = screen.getByLabelText('Nennleistung (kW)');
	expect(field).toHaveAttribute('aria-invalid', 'true');
	expect(field).toHaveAccessibleDescription('Zahl von 1 bis 100, z. B. 27');
});

it('sendet leere Felder als null und die Leistungszahl als Zahl', () => {
	const current = settingsCurrent().settings;
	const draft = draftFrom(current);
	const result = applyDraft(current, draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.settings.heating).toEqual({
		burner_kw: null,
		oil_l_per_h: null,
		oil_price_eur_l: null,
		ac_cop: 3
	});
	draft.heating = { burnerKw: '27', oilLPerH: '2,9', oilPriceEurL: '', acCop: '3,2' };
	const filled = applyDraft(current, draft);
	if (!('settings' in filled)) throw new Error(JSON.stringify(filled.errors));
	expect(filled.settings.heating).toEqual({
		burner_kw: 27,
		oil_l_per_h: 2.9,
		oil_price_eur_l: null,
		ac_cop: 3.2
	});
});

it('lehnt 0 kW und Werte außerhalb der Grenzen ab', () => {
	const current = settingsCurrent().settings;
	const draft = draftFrom(current);
	draft.heating = { burnerKw: '0', oilLPerH: '0', oilPriceEurL: '6', acCop: '' };
	const result = applyDraft(current, draft);
	expect(result).toEqual({
		errors: {
			'heating.burner_kw': 'Zahl von 1 bis 100, z. B. 27',
			'heating.oil_l_per_h': 'Zahl über 0 bis 20, z. B. 2,9',
			'heating.oil_price_eur_l': 'Zahl von 0 bis 5, z. B. 0,98',
			'heating.ac_cop': 'Zahl von 1 bis 8, z. B. 3,0'
		}
	});
});
