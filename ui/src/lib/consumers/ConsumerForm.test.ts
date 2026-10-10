import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import type { Candidates } from '$lib/api/schemas';
import ConsumerForm from './ConsumerForm.svelte';
import { emptyDraft } from './form';

// Home Assistant schreibt je Einheit eine Messgröße: nach einem Wechsel W → kW steht die Entität zweimal da
const candidates: Candidates = {
	core: [],
	ha: [
		{ entity_id: 'sensor.kueche_power', unit: 'W' },
		{ entity_id: 'sensor.kueche_power', unit: 'kW' },
		{ entity_id: 'sensor.waschmaschine_power', unit: 'W' }
	],
	ha_error: null
};

function renderForm(sourceKind: 'core' | 'ha', found: Candidates) {
	render(ConsumerForm, {
		draft: { ...emptyDraft(), sourceKind },
		consumers: [],
		candidates: found,
		editingId: null,
		onSave: vi.fn(),
		onCancel: vi.fn()
	});
}

it('schlägt eine HA-Entität mit zwei Einheiten einmal vor und rät die Einheit nicht', async () => {
	const draft = { ...emptyDraft(), sourceKind: 'ha' as const };
	const { container } = render(ConsumerForm, {
		draft,
		consumers: [],
		candidates,
		editingId: null,
		onSave: vi.fn(),
		onCancel: vi.fn()
	});
	const options = [...container.querySelectorAll('datalist option')].map((option) =>
		option.getAttribute('value')
	);
	expect(options).toEqual(['sensor.kueche_power', 'sensor.waschmaschine_power']);

	const ref = screen.getByRole('combobox', { name: 'HA-Entität' });
	await fireEvent.input(ref, { target: { value: 'sensor.kueche_power' } });
	await fireEvent.change(ref);
	expect(draft.powerUnit).toBe('');
	await fireEvent.input(ref, { target: { value: 'sensor.waschmaschine_power' } });
	await fireEvent.change(ref);
	expect(draft.powerUnit).toBe('W');
});

it('sagt, warum es keine HA-Vorschläge gibt', () => {
	const error = 'HA-Datenbank „homeassistant“ nicht lesbar: requires READ on homeassistant';
	renderForm('ha', { core: [], ha: [], ha_error: error });
	expect(screen.getByText(error)).toBeInTheDocument();
});

it('meldet eine leere HA-Datenbank nur bei HA als Quelle', () => {
	renderForm('ha', { core: [], ha: [], ha_error: null });
	expect(screen.getByText(/Keine Sensoren in W oder kW/)).toBeInTheDocument();
	document.body.innerHTML = '';
	renderForm('core', { core: [], ha: [], ha_error: 'HA-Datenbank nicht lesbar' });
	expect(screen.queryByText(/Keine Sensoren/)).not.toBeInTheDocument();
	expect(screen.queryByText('HA-Datenbank nicht lesbar')).not.toBeInTheDocument();
});
