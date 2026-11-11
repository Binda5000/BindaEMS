import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { PvCandidates, PvSource } from '$lib/api/schemas';
import { HUAWEI, SUED, WEST } from '$lib/testing/pv';
import { draftFromSource, draftToInput, emptyDraft, parentOptions } from './form';
import PvForm from './PvForm.svelte';

const BALKON: PvSource = { ...HUAWEI, id: 4, name: 'Balkon', kwp: null, sort: 1 };
const SOURCES = [HUAWEI, SUED, WEST, BALKON];
const CANDIDATES: PvCandidates = {
	core: ['pv.huawei.power_w', 'pv_dc.power_w'],
	ha: [
		{ entity_id: 'sensor.pv1_leistung', unit: 'W' },
		{ entity_id: 'sensor.pv2_leistung', unit: 'kW' }
	],
	ha_error: null
};

function renderForm(
	draft = emptyDraft(SOURCES),
	editingId: number | null = null,
	onSave = vi.fn(async () => {})
) {
	render(PvForm, {
		draft,
		sources: SOURCES,
		candidates: CANDIDATES,
		editingId,
		onSave,
		onCancel: vi.fn(),
		onDelete: vi.fn()
	});
	return onSave;
}

function parentChoices(): string[] {
	const select = screen.getByRole('combobox', { name: 'Gehört zu' });
	return within(select)
		.getAllByRole('option')
		.map((option) => option.textContent ?? '');
}

describe('PvForm', () => {
	it('bietet unter „Gehört zu“ nur Anlagen an, keine Strings und nicht sich selbst', () => {
		renderForm(draftFromSource(BALKON), BALKON.id);
		expect(parentChoices()).toEqual(['Keine (eigene Anlage)', 'Huawei']);
		document.body.innerHTML = '';
		renderForm(draftFromSource(SUED), SUED.id);
		expect(parentChoices()).toEqual(['Keine (eigene Anlage)', 'Huawei', 'Balkon']);
	});

	it('lässt eine Anlage mit Strings eine Anlage bleiben', () => {
		renderForm(draftFromSource(HUAWEI), HUAWEI.id);
		expect(screen.getByRole('combobox', { name: 'Gehört zu' })).toBeDisabled();
		expect(screen.getByText(/hat Strings/)).toBeInTheDocument();
	});

	it('liest kWp mit Komma und sendet die Zahl', async () => {
		const onSave = renderForm({
			...emptyDraft(SOURCES, HUAWEI.id),
			name: 'Süd',
			sourceKind: 'ha',
			powerRef: 'sensor.pv1_leistung',
			powerUnit: 'W'
		});
		await fireEvent.input(screen.getByLabelText(/^kWp/), { target: { value: '4,1' } });
		await fireEvent.submit(screen.getByRole('button', { name: 'Speichern' }));
		expect(onSave).toHaveBeenCalledWith({
			name: 'Süd',
			parent_id: 1,
			color: expect.stringMatching(/^#[0-9a-f]{6}$/i),
			source_kind: 'ha',
			power_ref: 'sensor.pv1_leistung',
			power_unit: 'W',
			kwp: 4.1,
			sort: 0
		});
	});

	it('meldet eine HA-Entität ohne Einheit und sendet nichts', async () => {
		const onSave = renderForm({
			...emptyDraft(SOURCES),
			name: 'Süd',
			sourceKind: 'ha',
			powerRef: 'sensor.pv_neu'
		});
		await fireEvent.submit(screen.getByRole('button', { name: 'Speichern' }));
		expect(screen.getByText('Einheit wählen (W oder kW)')).toBeInTheDocument();
		expect(onSave).not.toHaveBeenCalled();
	});

	it('schlägt core-Signale und HA-Sensoren vor und übernimmt die Einheit', async () => {
		const draft = { ...emptyDraft(SOURCES), sourceKind: 'ha' as const };
		const { container } = render(PvForm, {
			draft,
			sources: SOURCES,
			candidates: CANDIDATES,
			editingId: null,
			onSave: vi.fn(),
			onCancel: vi.fn(),
			onDelete: vi.fn()
		});
		const options = () =>
			[...container.querySelectorAll('datalist option')].map((option) =>
				option.getAttribute('value')
			);
		expect(options()).toEqual(['sensor.pv1_leistung', 'sensor.pv2_leistung']);
		const ref = screen.getByRole('combobox', { name: 'HA-Entität' });
		await fireEvent.input(ref, { target: { value: 'sensor.pv2_leistung' } });
		await fireEvent.change(ref);
		expect(draft.powerUnit).toBe('kW');
	});

	it('schlägt für core die PV-Signale als Text vor', () => {
		const { container } = render(PvForm, {
			draft: emptyDraft(SOURCES),
			sources: SOURCES,
			candidates: CANDIDATES,
			editingId: null,
			onSave: vi.fn(),
			onCancel: vi.fn(),
			onDelete: vi.fn()
		});
		const options = [...container.querySelectorAll('datalist option')].map((option) =>
			option.getAttribute('value')
		);
		expect(options).toEqual(['pv.huawei.power_w', 'pv_dc.power_w']);
		expect(screen.getByRole('radio', { name: 'Victron (core)' })).toBeChecked();
	});

	it('bietet beim Ändern „Löschen“ an, beim Anlegen nicht', () => {
		renderForm(draftFromSource(SUED), SUED.id);
		expect(screen.getByRole('button', { name: 'Löschen' })).toBeInTheDocument();
		document.body.innerHTML = '';
		renderForm();
		expect(screen.queryByRole('button', { name: 'Löschen' })).not.toBeInTheDocument();
	});
});

describe('draftToInput', () => {
	it('prüft Name, Signal, kWp und Reihenfolge wie der Server', () => {
		const result = draftToInput({
			...emptyDraft([]),
			name: ' ',
			powerRef: 'load.buero.power_w',
			kwp: '0,05',
			sort: '1,5'
		});
		expect(result).toEqual({
			errors: {
				name: 'Name fehlt',
				powerRef: 'core-Signal muss ein PV-Signal sein, z. B. pv.huawei.power_w oder pv_dc.power_w',
				kwp: 'kWp zwischen 0,1 und 1000 oder leer',
				sort: 'ganze Zahl zwischen -1000000 und 1000000'
			}
		});
	});

	it('sendet ohne kWp null und für core keine Einheit', () => {
		expect(
			draftToInput({
				...emptyDraft([]),
				name: 'Huawei',
				powerRef: 'pv.huawei.power_w',
				powerUnit: 'kW',
				kwp: ' '
			})
		).toEqual({
			input: {
				name: 'Huawei',
				parent_id: null,
				color: expect.any(String),
				source_kind: 'core',
				power_ref: 'pv.huawei.power_w',
				power_unit: null,
				kwp: null,
				sort: 0
			}
		});
	});

	it('nimmt eine noch nicht benutzte Farbe für einen neuen Eintrag', () => {
		expect(emptyDraft([]).color).toBe('#d18f00');
		expect(emptyDraft([SUED]).color).toBe('#1f6feb');
	});

	it('nennt die möglichen Anlagen', () => {
		expect(parentOptions(SOURCES, null).map((item) => item.name)).toEqual(['Huawei', 'Balkon']);
	});
});
