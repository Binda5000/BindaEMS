import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { SensorCandidates } from '$lib/api/schemas';
import { sensorCandidates, settingsCurrent } from '$lib/testing/fixtures';
import LearningEditorHost from '$lib/testing/LearningEditorHost.svelte';
import { draftFrom, type LearningDraft } from './model';

function renderEditor(
	options: {
		load?: () => Promise<SensorCandidates>;
		draft?: Partial<LearningDraft>;
		errors?: Record<string, string>;
	} = {}
) {
	const probe: { draft: LearningDraft | null } = { draft: null };
	const load = options.load ?? vi.fn().mockResolvedValue(sensorCandidates());
	render(LearningEditorHost, {
		initial: { ...draftFrom(settingsCurrent().settings).learning, ...options.draft },
		errors: options.errors,
		loadCandidates: load,
		probe
	});
	const draft = () => {
		if (!probe.draft) throw new Error('Entwurf fehlt');
		return probe.draft;
	};
	return { draft, load };
}

const many = (count: number, prefix = 'sensor.x') =>
	Array.from({ length: count }, (_, index) => `${prefix}${index}`);

it('zeigt den Schalter „Lastmodell verwenden“ an und schaltet ihn', async () => {
	const { draft } = renderEditor();
	expect(screen.getByRole('group', { name: 'Lastmodell' })).toBeInTheDocument();
	const toggle = screen.getByRole('checkbox', { name: 'Lastmodell verwenden' });
	expect(toggle).toBeChecked();
	expect(
		screen.getByText('Aus: Die Planung nutzt immer das Profil. Das Modell rechnet weiter mit.')
	).toBeInTheDocument();
	await fireEvent.click(toggle);
	expect(draft().loadModelEnabled).toBe(false);
	expect(
		screen.getByRole('group', { name: 'Zusätzliche Sensoren aus Home Assistant (höchstens 8)' })
	).toHaveAccessibleDescription(/Zum Beispiel Anwesenheit \(person\.…\)/);
});

it('wählt Sensoren aus der Liste und nimmt sie wieder heraus', async () => {
	const { draft, load } = renderEditor({ draft: { haSensors: [] } });
	const presence = await screen.findByRole('checkbox', { name: 'binary_sensor.anwesenheit' });
	expect(
		screen.getByRole('checkbox', { name: 'sensor.aussentemperatur (°C)' })
	).toBeInTheDocument();
	await fireEvent.click(presence);
	await fireEvent.click(screen.getByRole('checkbox', { name: 'sensor.aussentemperatur (°C)' }));
	expect(draft().haSensors).toEqual(['binary_sensor.anwesenheit', 'sensor.aussentemperatur']);
	const chosen = screen.getByRole('list', { name: 'Gewählte Sensoren' });
	expect(within(chosen).getAllByRole('listitem')).toHaveLength(2);
	await fireEvent.click(
		within(chosen).getByRole('button', { name: 'Sensor binary_sensor.anwesenheit entfernen' })
	);
	expect(draft().haSensors).toEqual(['sensor.aussentemperatur']);
	expect(presence).not.toBeChecked();
	await fireEvent.click(screen.getByRole('checkbox', { name: 'sensor.aussentemperatur (°C)' }));
	expect(draft().haSensors).toEqual([]);
	expect(load).toHaveBeenCalledTimes(1);
});

it('sucht in der Liste', async () => {
	renderEditor({ draft: { haSensors: [] } });
	await screen.findByRole('checkbox', { name: 'binary_sensor.anwesenheit' });
	await fireEvent.input(screen.getByLabelText('Sensoren suchen'), { target: { value: 'wohn' } });
	expect(screen.getAllByRole('checkbox', { name: /sensor\./ })).toHaveLength(1);
	expect(screen.getByRole('checkbox', { name: /wohnzimmer/ })).toBeInTheDocument();
	await fireEvent.input(screen.getByLabelText('Sensoren suchen'), { target: { value: 'xyz' } });
	expect(screen.getByText('Keine Entität gefunden.')).toBeInTheDocument();
});

it('erlaubt höchstens 8 Sensoren', async () => {
	const { draft } = renderEditor({ draft: { haSensors: many(7) } });
	const first = await screen.findByRole('checkbox', { name: 'binary_sensor.anwesenheit' });
	const second = screen.getByRole('checkbox', { name: 'sensor.aussentemperatur (°C)' });
	await fireEvent.click(first);
	expect(draft().haSensors).toHaveLength(8);
	// die Auswahl ist danach gesperrt, die gewählten bleiben abwählbar
	expect(second).toBeDisabled();
	expect(first).toBeEnabled();
	expect(
		screen.getByText(/Es sind 8 Sensoren gewählt, das ist die Obergrenze/)
	).toBeInTheDocument();
	await fireEvent.click(first);
	expect(draft().haSensors).toHaveLength(7);
	expect(second).toBeEnabled();
});

it('nennt den Grund, wenn die HA-Datenbank fehlt, und erlaubt das Entfernen', async () => {
	const load = vi.fn().mockResolvedValue({
		entities: [],
		error: 'HA-Datenbank nicht eingerichtet (influxdb.ha_database)'
	});
	const { draft } = renderEditor({ load });
	expect(
		await screen.findByText('HA-Datenbank nicht eingerichtet (influxdb.ha_database)')
	).toBeInTheDocument();
	expect(screen.queryByLabelText('Sensoren suchen')).toBeNull();
	// ohne Liste weiß man nicht, ob ein Sensor fehlt
	expect(screen.queryByText('nicht in HA gefunden')).toBeNull();
	await fireEvent.click(
		screen.getByRole('button', { name: 'Sensor sensor.aussentemperatur entfernen' })
	);
	expect(draft().haSensors).toEqual(['sensor.wohnzimmer_temperatur']);
});

it('nennt den Grund, wenn die Abfrage scheitert', async () => {
	const load = vi.fn().mockRejectedValue(new ApiError(500, 'Interner Fehler'));
	renderEditor({ load });
	expect(await screen.findByText('Interner Fehler')).toBeInTheDocument();
	expect(screen.getByText('sensor.aussentemperatur')).toBeInTheDocument();
});

it('zeigt einen gespeicherten Sensor, der nicht mehr in HA steht, mit Hinweis', async () => {
	const { draft } = renderEditor({
		draft: { haSensors: ['sensor.aussentemperatur', 'sensor.weg'] }
	});
	await screen.findByRole('checkbox', { name: 'binary_sensor.anwesenheit' });
	const items = within(screen.getByRole('list', { name: 'Gewählte Sensoren' })).getAllByRole(
		'listitem'
	);
	expect(items[0]).not.toHaveTextContent('nicht in HA gefunden');
	expect(items[1]).toHaveTextContent('sensor.weg');
	expect(items[1]).toHaveTextContent('nicht in HA gefunden');
	await fireEvent.click(within(items[1]).getByRole('button', { name: /entfernen/ }));
	expect(draft().haSensors).toEqual(['sensor.aussentemperatur']);
});

it('lädt die Kandidaten nicht ohne Funktion und zeigt die Fehler der Sensorliste', () => {
	const probe: { draft: LearningDraft | null } = { draft: null };
	render(LearningEditorHost, {
		initial: draftFrom(settingsCurrent().settings).learning,
		errors: {
			'learning.ha_sensors': 'Höchstens 8 Sensoren, jeder nur einmal',
			'learning.ha_sensors.1': 'ungültig'
		},
		probe
	});
	expect(screen.getByText('Höchstens 8 Sensoren, jeder nur einmal')).toBeInTheDocument();
	expect(screen.getByText('ungültig')).toBeInTheDocument();
	expect(screen.queryByLabelText('Sensoren suchen')).toBeNull();
});

it('begrenzt die Treffer der Liste', async () => {
	const entities = many(130, 'sensor.t').map((entity_id) => ({ entity_id, unit: null }));
	renderEditor({
		load: vi.fn().mockResolvedValue({ entities, error: null }),
		draft: { haSensors: [] }
	});
	await screen.findByRole('checkbox', { name: 'sensor.t0' });
	expect(screen.getAllByRole('checkbox', { name: /sensor\.t/ })).toHaveLength(100);
	expect(screen.getByText('30 weitere Treffer; den Suchbegriff eingrenzen.')).toBeInTheDocument();
});
