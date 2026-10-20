import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import error422 from '$lib/api/contract/error-422.json';
import { limits, sensorCandidates, settingsCurrent } from '$lib/testing/fixtures';
import SettingsEditor from './SettingsEditor.svelte';

const LOSS = () =>
	within(screen.getByRole('group', { name: 'Netzverlustentgelt' })).getByLabelText('Wert netto');
const GRID = 'Netznutzungsentgelt Arbeitspreis (Netzebene 7)';

/** 422 der API mit den Pfaden, wie pydantic sie für Modell-Prüfungen liefert */
const reject422 = (...issues: { loc: (string | number)[]; msg: string }[]) =>
	vi.fn().mockRejectedValueOnce(
		new ApiError(422, 'x', {
			detail: issues.map((issue) => ({
				type: 'value_error',
				loc: ['body', 'settings', ...issue.loc],
				msg: `Value error, ${issue.msg}`
			}))
		})
	);

it('Konflikt behält die Eingaben und bietet Neuladen an', async () => {
	const save = vi
		.fn()
		.mockRejectedValueOnce(
			new ApiError(409, 'Die Einstellungen wurden inzwischen geändert (aktuell Version 7).')
		);
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(LOSS(), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(
		await screen.findByText('Die Einstellungen wurden inzwischen geändert (aktuell Version 7).')
	).toBeInTheDocument();
	expect(LOSS()).toHaveValue('1,23');
	expect(
		screen.getByRole('button', { name: 'Neu laden (Eingaben verwerfen)' })
	).toBeInTheDocument();
});

it('Serverfehler erscheinen am Feld', async () => {
	const save = vi.fn().mockRejectedValueOnce(new ApiError(422, 'x', { detail: error422.detail }));
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(LOSS(), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('Zahl erwartet')).toBeInTheDocument();
	expect(screen.getByText('Bitte die markierten Felder prüfen.')).toBeInTheDocument();
	// der Fokus liegt auf dem ersten fehlerhaften Feld
	await vi.waitFor(() =>
		expect(
			within(screen.getByRole('group', { name: GRID })).getByLabelText('Wert netto')
		).toHaveFocus()
	);
});

it('Serverfehler einer Gruppe: Hinweis oben, Fokus auf dem Fehlertext', async () => {
	const save = reject422({
		loc: ['tariff'],
		msg: 'Leistungspreise dürfen sich zeitlich nicht überschneiden'
	});
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('Bitte die markierten Felder prüfen.')).toBeInTheDocument();
	await vi.waitFor(() =>
		expect(
			screen.getByText('Leistungspreise dürfen sich zeitlich nicht überschneiden')
		).toHaveFocus()
	);
});

it('Prüfung im Editor: Hinweis oben und Fokus auf dem ersten fehlerhaften Feld', async () => {
	const save = vi.fn();
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(screen.getByLabelText('USt. (%)'), { target: { value: 'abc' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('Bitte die markierten Felder prüfen.')).toBeInTheDocument();
	await vi.waitFor(() => expect(screen.getByLabelText('USt. (%)')).toHaveFocus());
	expect(save).not.toHaveBeenCalled();
});

it('Prüfung im Editor: ohne Feld (Monate) bekommt der Fehlertext den Fokus', async () => {
	render(SettingsEditor, {
		current: settingsCurrent(),
		save: vi.fn(),
		onSaved: vi.fn(),
		onReload: vi.fn()
	});
	const window = within(screen.getByRole('group', { name: GRID })).getByRole('group', {
		name: 'Zeitfenster 1'
	});
	for (const month of ['Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep']) {
		await fireEvent.click(within(window).getByRole('button', { name: month }));
	}
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('Bitte die markierten Felder prüfen.')).toBeInTheDocument();
	await vi.waitFor(() => expect(within(window).getByText('Mindestens ein Monat')).toHaveFocus());
});

it('meldet nach dem Speichern, ob sich Tarifwerte geändert haben', async () => {
	const current = settingsCurrent();
	const saved = { ...current, version: current.version + 1 };
	const onSaved = vi.fn();
	render(SettingsEditor, {
		current,
		save: vi.fn().mockResolvedValueOnce(saved),
		onSaved,
		onReload: vi.fn()
	});
	await fireEvent.input(LOSS(), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true));
});

it('vergibt eigene Namen für Ladestationen; leer heißt Typname', async () => {
	const current = settingsCurrent();
	const save = vi.fn().mockResolvedValueOnce(current);
	render(SettingsEditor, {
		current,
		wallboxes: limits().wallboxes,
		save,
		onSaved: vi.fn(),
		onReload: vi.fn()
	});
	// Demo-Welt: der Wall Connector heißt „Garage“, die EVCS hat keinen eigenen Namen
	const evcs = screen.getByLabelText('evcs (EVCS)');
	const twc = screen.getByLabelText('twc (Wall Connector)');
	expect([evcs, twc].map((input) => (input as HTMLInputElement).value)).toEqual(['', 'Garage']);
	await fireEvent.input(evcs, { target: { value: '  Carport ' } });
	await fireEvent.input(twc, { target: { value: '' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(save).toHaveBeenCalled());
	expect(save.mock.calls[0][1].wallbox_names).toEqual({ evcs: 'Carport' });
});

it('zeigt Regelfehler des Servers am Zeitfenster, am Bestandteil und am Tarif', async () => {
	const save = reject422(
		{
			loc: ['tariff', 'components', 2, 'energy_per_kwh', 'windows', 0],
			msg: 'Ende muss nach dem Beginn liegen (00:00 = Tagesende)'
		},
		{
			loc: ['tariff', 'components', 6, 'power_per_kw_year'],
			msg: 'valid_from liegt nach valid_until'
		},
		{ loc: ['tariff'], msg: 'Leistungspreise dürfen sich zeitlich nicht überschneiden' }
	);
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	const window = within(screen.getByRole('group', { name: GRID })).getByRole('group', {
		name: 'Zeitfenster 1'
	});
	expect(
		await within(window).findByText('Ende muss nach dem Beginn liegen (00:00 = Tagesende)')
	).toBeInTheDocument();
	expect(
		within(screen.getByRole('group', { name: 'Leistungspreis (ElWG)' })).getByText(
			'valid_from liegt nach valid_until'
		)
	).toBeInTheDocument();
	expect(
		screen.getByText('Leistungspreise dürfen sich zeitlich nicht überschneiden')
	).toBeInTheDocument();
	// nichts davon landet zusätzlich in der Meldung oben
	expect(screen.queryByText(/tariff(\.components)?[.:]/)).toBeNull();
});

it('nennt Fehler ohne Platz im Editor oben mit dem Schlüssel', async () => {
	const save = reject422({
		loc: ['tariff', 'components', 2, 'energy_per_kwh', 'windows', 0, 'months', 1],
		msg: 'Einträge dürfen nicht doppelt vorkommen'
	});
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(
		await screen.findByText(/tariff\.components\.2\.windows\.0\.months\.1: Einträge dürfen nicht/)
	).toBeInTheDocument();
});

it('speichert das geänderte Zeitfenster und meldet die Tarifänderung', async () => {
	const current = settingsCurrent();
	const saved = { ...current, version: current.version + 1 };
	const save = vi.fn().mockResolvedValueOnce(saved);
	const onSaved = vi.fn();
	render(SettingsEditor, { current, save, onSaved, onReload: vi.fn() });
	const window = within(screen.getByRole('group', { name: GRID })).getByRole('group', {
		name: 'Zeitfenster 1'
	});
	await fireEvent.input(within(window).getByLabelText('Wert'), { target: { value: '0,75' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true));
	const grid = save.mock.calls[0][1].tariff.components[2];
	expect(grid.windows[0].factor).toBe(0.75);
});

it('prüft das Zeitfenster vor dem Speichern und zeigt den Fehler am Feld', async () => {
	const save = vi.fn();
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	const window = within(screen.getByRole('group', { name: GRID })).getByRole('group', {
		name: 'Zeitfenster 1'
	});
	await fireEvent.click(within(window).getByRole('button', { name: 'Mai' }));
	for (const month of ['Apr', 'Jun', 'Jul', 'Aug', 'Sep']) {
		await fireEvent.click(within(window).getByRole('button', { name: month }));
	}
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await within(window).findByText('Mindestens ein Monat')).toBeInTheDocument();
	expect(save).not.toHaveBeenCalled();
});

it('fügt einen Bestandteil hinzu und speichert ihn mit fester ID', async () => {
	const current = settingsCurrent();
	const save = vi.fn().mockResolvedValueOnce(current);
	render(SettingsEditor, { current, save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'fixed_per_month' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Bestandteil hinzufügen' }));
	const added = screen.getByRole('group', { name: 'Neue Gebühr' });
	await fireEvent.input(within(added).getByLabelText('Wert netto'), { target: { value: '3,5' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(save).toHaveBeenCalled());
	expect(save.mock.calls[0][1].tariff.components.at(-1)).toMatchObject({
		id: 'neue_gebuehr',
		kind: 'fixed_per_month',
		value_eur: 3.5
	});
});

it('speichert Schalter und Sensoren des Lastmodells mit den übrigen Einstellungen', async () => {
	const current = settingsCurrent();
	const save = vi.fn().mockResolvedValueOnce(current);
	const loadCandidates = vi.fn().mockResolvedValue(sensorCandidates());
	render(SettingsEditor, { current, save, loadCandidates, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.click(await screen.findByRole('checkbox', { name: 'binary_sensor.anwesenheit' }));
	await fireEvent.click(screen.getByRole('checkbox', { name: 'Lastmodell verwenden' }));
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(save).toHaveBeenCalled());
	expect(save).toHaveBeenCalledTimes(1);
	expect(save.mock.calls[0][1].learning).toEqual({
		load_model_enabled: false,
		ha_sensors: [
			'sensor.aussentemperatur',
			'sensor.wohnzimmer_temperatur',
			'binary_sensor.anwesenheit'
		]
	});
	expect(loadCandidates).toHaveBeenCalledTimes(1);
});

it('zeigt Serverfehler der Sensorliste am Lastmodell', async () => {
	const save = reject422({ loc: ['learning', 'ha_sensors', 0], msg: 'keine gültige Entität' });
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('keine gültige Entität')).toBeInTheDocument();
	expect(screen.getByText('Bitte die markierten Felder prüfen.')).toBeInTheDocument();
});
