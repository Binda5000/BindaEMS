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
	]
};

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
