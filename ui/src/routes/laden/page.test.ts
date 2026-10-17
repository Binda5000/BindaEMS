import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import * as v from 'valibot';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import controlJson from '$lib/api/contract/control.json';
import { ApiError } from '$lib/api/errors';
import {
	ControlResponseSchema,
	SystemResponseSchema,
	type ControlReply,
	type CoreHealth,
	type Intent,
	type Role,
	type VehicleHealth,
	type WallboxHealth
} from '$lib/api/schemas';
import { limits, system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, user } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		control: vi.fn(),
		limits: vi.fn(),
		setRegulation: vi.fn(),
		setMode: vi.fn(),
		setBoost: vi.fn(),
		setWallboxVehicle: vi.fn(),
		setGuestMode: vi.fn(),
		setSupport: vi.fn(),
		setPriority: vi.fn()
	},
	user: { role: 'operator' as Role }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/state', () => ({
	page: {
		data: {
			get user() {
				return { id: 1, username: 'bedienung', role: user.role };
			}
		}
	}
}));

function baseIntent(changes: Partial<Intent> = {}): Intent {
	const { intent } = v.parse(ControlResponseSchema, controlJson);
	if (intent === null) throw new Error('control.json enthält keine Absicht');
	return { ...intent, ...changes };
}

function reply(intent: Intent = baseIntent(), changes: Partial<ControlReply> = {}): ControlReply {
	return { result: 'accepted', version: intent.version + 1, details: [], intent, ...changes };
}

function healthWith(changes: Partial<CoreHealth> = {}) {
	const base = system();
	return { ...base, core: { ...base.core, health: { ...base.core.health!, ...changes } } };
}

function vehicleAt(
	health: CoreHealth,
	id: string,
	changes: Partial<VehicleHealth>
): VehicleHealth[] {
	return health.vehicles.map((vehicle) =>
		vehicle.id === id ? { ...vehicle, ...changes } : vehicle
	);
}

function wallboxAt(
	health: CoreHealth,
	id: string,
	changes: Partial<WallboxHealth>
): WallboxHealth[] {
	return health.wallboxes.map((wallbox) =>
		wallbox.id === id ? { ...wallbox, ...changes } : wallbox
	);
}

/** Rendert die Seite mit dieser Antwort von /api/system und der Absicht der Vertragsdatei */
async function show(response = system(), intent: Intent | null = baseIntent()) {
	api.system.mockResolvedValue(response);
	api.control.mockResolvedValue({ core_connected: true, intent });
	api.limits.mockResolvedValue(limits());
	render(Page);
	await screen.findByRole('heading', { level: 2, name: 'Tesla Model 3' });
}

function card(title: string): HTMLElement {
	const section = screen.getByRole('heading', { level: 2, name: title }).closest('section');
	if (section === null) throw new Error(`Karte „${title}“ fehlt`);
	return section;
}

beforeEach(() => {
	user.role = 'operator';
	for (const mock of Object.values(api)) mock.mockReset();
});

afterEach(() => {
	vi.useRealTimers();
});

it('Bedienen wählt einen Lademodus', async () => {
	await show();
	api.setMode.mockResolvedValue(reply());
	const select = within(card('Tesla Model 3')).getByRole('combobox', { name: 'Lademodus' });
	expect(select).toHaveValue('min_pv');
	expect(
		within(select)
			.getAllByRole('option')
			.map((o) => o.textContent)
	).toEqual(['PV-Überschuss', 'Min+PV', 'Sofort', 'Aus']);
	const loaded = api.control.mock.calls.length;
	await fireEvent.change(select, { target: { value: 'now' } });
	await waitFor(() => expect(api.setMode).toHaveBeenCalledWith('tesla', 'now'));
	await waitFor(() => expect(api.control.mock.calls.length).toBeGreaterThan(loaded));
});

it('lädt das System zwei Sekunden nach einer Änderung neu', async () => {
	await show();
	api.setMode.mockResolvedValue(reply());
	vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
	const loaded = api.system.mock.calls.length;
	await fireEvent.change(within(card('e-Golf')).getByRole('combobox', { name: 'Lademodus' }), {
		target: { value: 'off' }
	});
	await vi.advanceTimersByTimeAsync(1900);
	expect(api.system).toHaveBeenCalledTimes(loaded);
	await vi.advanceTimersByTimeAsync(200);
	expect(api.system).toHaveBeenCalledTimes(loaded + 1);
});

it('zeigt die Fakten eines Fahrzeugs', async () => {
	const base = system();
	const health = base.core.health!;
	await show(
		healthWith({
			vehicles: vehicleAt(health, 'tesla', {
				plugged: true,
				charging: true,
				limit_a: 10,
				commands_hour: 3,
				reason: 'mode_min_pv',
				assignment: 'detected'
			})
		})
	);
	const tesla = within(card('Tesla Model 3'));
	const fact = (label: string) => tesla.getByText(label, { selector: 'dt' }).nextElementSibling;
	expect(fact('Wallbox')).toHaveTextContent('Garage (erkannt)');
	expect(fact('Angesteckt')).toHaveTextContent('ja');
	expect(fact('Lädt')).toHaveTextContent('ja');
	expect(fact('Grenze')).toHaveTextContent('10 A');
	expect(fact('Letzter Grund')).toHaveTextContent('Min+PV');
	expect(fact('Befehle letzte Stunde')).toHaveTextContent('3');
	// e-Golf hat kein Tessie: keine Zahl der Befehle
	expect(within(card('e-Golf')).queryByText('Befehle letzte Stunde')).not.toBeInTheDocument();
});

it('Boost sendet die Taste „Boost“', async () => {
	await show();
	api.setBoost.mockResolvedValue(reply());
	await fireEvent.click(within(card('Tesla Model 3')).getByRole('button', { name: 'Boost' }));
	await waitFor(() => expect(api.setBoost).toHaveBeenCalledWith('tesla', true));
});

it('Boost zeigt „Boost beenden“ mit Uhrzeit, solange er gilt', async () => {
	const health = system().core.health!;
	await show(
		healthWith({
			vehicles: vehicleAt(health, 'tesla', {
				mode: 'boost',
				boost_until: '2026-10-09T12:00:00+00:00'
			})
		})
	);
	const tesla = within(card('Tesla Model 3'));
	expect(tesla.getByText('bis 14:00')).toBeInTheDocument();
	expect(tesla.queryByRole('button', { name: 'Boost' })).not.toBeInTheDocument();
	api.setBoost.mockResolvedValue(reply());
	await fireEvent.click(tesla.getByRole('button', { name: 'Boost beenden' }));
	await waitFor(() => expect(api.setBoost).toHaveBeenCalledWith('tesla', false));
});

it('Boost wartet auf das Anstecken, solange die Absicht ihn hält', async () => {
	const health = system().core.health!;
	const future = new Date(Date.now() + 6 * 3_600_000).toISOString();
	const intent = baseIntent({
		vehicles: { tesla: { mode: 'min_pv', boost_until: future, wallbox: null } }
	});
	await show(
		healthWith({
			vehicles: vehicleAt(health, 'tesla', { boost_pending: true, boost_until: null })
		}),
		intent
	);
	const tesla = within(card('Tesla Model 3'));
	expect(tesla.getByText('wartet auf Anstecken')).toBeInTheDocument();
	expect(tesla.getByText(/^bis \d\d:\d\d$/)).toBeInTheDocument();
	expect(tesla.getByRole('button', { name: 'Boost beenden' })).toBeInTheDocument();
});

it('Lesen sieht keine Bedienelemente, aber die Werte', async () => {
	user.role = 'viewer';
	const health = system().core.health!;
	await show(
		healthWith({
			vehicles: vehicleAt(health, 'tesla', {
				mode: 'boost',
				boost_until: '2026-10-09T12:00:00+00:00'
			})
		}),
		baseIntent({ control: false })
	);
	expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
	expect(screen.queryByRole('button')).not.toBeInTheDocument();
	const tesla = within(card('Tesla Model 3'));
	expect(tesla.getByText('Boost', { selector: 'dd' })).toBeInTheDocument();
	expect(tesla.getByText('bis 14:00')).toBeInTheDocument();
	expect(screen.getByText(/Regelbetrieb ist aus\./)).toBeInTheDocument();
});

it('Hinweis bei ausgeschaltetem Regelbetrieb', async () => {
	await show(system(), baseIntent({ control: false }));
	expect(
		screen.getByText(
			'Regelbetrieb ist aus. Der core regelt nichts; Modi und Boost wirken erst mit Regelbetrieb.'
		)
	).toBeInTheDocument();
	api.setRegulation.mockResolvedValue(reply());
	await fireEvent.click(screen.getByRole('button', { name: 'Regelbetrieb einschalten' }));
	await waitFor(() => expect(api.setRegulation).toHaveBeenCalledWith(true));
});

it('zeigt den Hinweis nicht bei eingeschaltetem Regelbetrieb', async () => {
	await show();
	expect(screen.queryByText(/Regelbetrieb ist aus/)).not.toBeInTheDocument();
});

it('zeigt eine Ablehnung des core an der Karte', async () => {
	await show();
	const text = 'Wallbox „twc“ ist mehreren Fahrzeugen zugeordnet.';
	api.setWallboxVehicle.mockRejectedValue(new ApiError(422, text));
	const select = within(card('Garage')).getByRole('combobox', { name: 'Zuordnung' });
	await fireEvent.change(select, { target: { value: 'egolf' } });
	const notice = await within(card('Garage')).findByText(text);
	expect(notice.closest('.notice')).toHaveClass('error');
	expect(within(card('Tesla Model 3')).queryByText(text)).not.toBeInTheDocument();
	expect(api.setWallboxVehicle).toHaveBeenCalledWith('twc', 'egolf');
	// die Auswahl springt auf den Stand des Servers zurück
	await waitFor(() => expect(select).toHaveValue(''));
});

it('zeigt einen Hinweis des core bei „clamped“ mit den Details', async () => {
	await show();
	api.setMode.mockResolvedValue(
		reply(baseIntent(), { result: 'clamped', details: ['Erster Hinweis.', 'Zweiter Hinweis.'] })
	);
	await fireEvent.change(
		within(card('Tesla Model 3')).getByRole('combobox', { name: 'Lademodus' }),
		{ target: { value: 'now' } }
	);
	const first = await within(card('Tesla Model 3')).findByText(/Erster Hinweis\./);
	expect(first.closest('.notice')).toHaveClass('warning');
	expect(first).toHaveTextContent('Zweiter Hinweis.');
});

it('Zuordnung je Wallbox mit Indizien', async () => {
	const health = system().core.health!;
	await show(
		healthWith({
			wallboxes: wallboxAt(health, 'evcs', {
				occupant: 'tesla',
				name: 'Tesla Model 3',
				assignment: 'detected',
				indicators: [
					{ vehicle: 'tesla', clue: 'current', supports: true },
					{ vehicle: 'egolf', clue: 'phases', supports: false }
				]
			})
		})
	);
	const evcs = within(card('EVCS'));
	expect(evcs.getByText('Tesla Model 3', { selector: 'dd' })).toBeInTheDocument();
	expect(evcs.getByText('erkannt')).toBeInTheDocument();
	expect(evcs.getAllByRole('listitem').map((item) => item.textContent)).toEqual([
		'Tesla Model 3: Strom passt zu Tessie',
		'e-Golf: Phasenzahl passt nicht'
	]);
	const select = evcs.getByRole('combobox', { name: 'Zuordnung' });
	expect(
		within(select)
			.getAllByRole('option')
			.map((o) => o.textContent)
	).toEqual(['Automatisch', 'Tesla Model 3', 'e-Golf']);
	expect(select).toHaveValue('');
	api.setWallboxVehicle.mockResolvedValueOnce(reply());
	await fireEvent.change(select, { target: { value: 'tesla' } });
	await waitFor(() => expect(api.setWallboxVehicle).toHaveBeenCalledWith('evcs', 'tesla'));
	api.setWallboxVehicle.mockResolvedValueOnce(reply());
	await fireEvent.change(select, { target: { value: '' } });
	await waitFor(() => expect(api.setWallboxVehicle).toHaveBeenLastCalledWith('evcs', null));
});

it('zeigt eine Wallbox ohne Vorgang', async () => {
	await show();
	expect(within(card('EVCS')).getByText('kein Fahrzeug')).toBeInTheDocument();
});

it('zeigt die Übersteuerung einer Wallbox in der Auswahl', async () => {
	const health = system().core.health!;
	const intent = baseIntent({
		vehicles: { egolf: { mode: 'pv', boost_until: null, wallbox: 'twc' } }
	});
	await show(
		healthWith({
			vehicles: vehicleAt(health, 'egolf', { wallbox: 'twc', assignment: 'override' }),
			wallboxes: wallboxAt(health, 'twc', {
				occupant: 'egolf',
				name: 'e-Golf',
				assignment: 'override'
			})
		}),
		intent
	);
	expect(within(card('Garage')).getByRole('combobox', { name: 'Zuordnung' })).toHaveValue('egolf');
});

it('wählt den Gast-Modus', async () => {
	await show();
	api.setGuestMode.mockResolvedValue(reply());
	const select = within(card('Gast')).getByRole('combobox', { name: 'Lademodus' });
	expect(select).toHaveValue('pv');
	await fireEvent.change(select, { target: { value: 'now' } });
	await waitFor(() => expect(api.setGuestMode).toHaveBeenCalledWith('now'));
});

it('zeigt die Akku-Unterstützung mit der wirksamen Schwelle', async () => {
	const health = system().core.health!;
	await show(
		healthWith({
			battery: { ...health.battery, support_soc_pct: 82, support_source: 'plan' }
		})
	);
	const support = within(card('Akku-Unterstützung'));
	expect(support.getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' })).toBeChecked();
	expect(support.getByRole('spinbutton', { name: 'Unterstützungsschwelle' })).toHaveValue(null);
	expect(support.getByText('Wirksam ab 82 % (Plan).')).toBeVisible();
});

it('kennt die wirksame Schwelle vor dem ersten Zyklus nicht', async () => {
	const health = system().core.health!;
	await show(
		healthWith({ battery: { ...health.battery, support_soc_pct: null, support_source: null } })
	);
	expect(
		within(card('Akku-Unterstützung')).getByText('Wirksame Schwelle unbekannt.')
	).toBeVisible();
});

it('kennt die Schwelle nicht, wenn Health keinen Akku-Block hat', async () => {
	const health: Partial<CoreHealth> = { ...system().core.health! };
	delete health.battery;
	const parsed = v.parse(SystemResponseSchema, {
		...system(),
		core: { connected: true, health }
	});
	await show(parsed);
	expect(
		within(card('Akku-Unterstützung')).getByText('Wirksame Schwelle unbekannt.')
	).toBeVisible();
	expect(screen.getByRole('heading', { level: 2, name: 'e-Golf' })).toBeVisible();
});

it('schaltet die Akku-Unterstützung aus', async () => {
	await show();
	api.setSupport.mockResolvedValue(reply());
	const box = within(card('Akku-Unterstützung')).getByRole('checkbox', {
		name: 'Akku-Unterstützung erlaubt'
	});
	await fireEvent.click(box);
	await waitFor(() => expect(api.setSupport).toHaveBeenCalledWith(false, null));
});

it('übernimmt die Schwelle und prüft sie gegen die USV-Reserve der Grenzen', async () => {
	await show();
	api.setSupport.mockResolvedValue(reply());
	const support = within(card('Akku-Unterstützung'));
	const field = support.getByRole('spinbutton', { name: 'Unterstützungsschwelle' });
	expect(field).toHaveAttribute('placeholder', '70');
	await fireEvent.input(field, { target: { value: '10' } });
	await fireEvent.click(support.getByRole('button', { name: 'Übernehmen' }));
	expect(
		support.getByText('Unterstützungsschwelle „10“ liegt außerhalb von 20–100 %.')
	).toBeVisible();
	expect(api.setSupport).not.toHaveBeenCalled();
	await fireEvent.input(field, { target: { value: '55' } });
	await fireEvent.click(support.getByRole('button', { name: 'Übernehmen' }));
	await waitFor(() => expect(api.setSupport).toHaveBeenCalledWith(true, 55));
});

it('zeigt die Ablehnung des core an der Karte der Akku-Unterstützung', async () => {
	await show();
	api.setSupport.mockRejectedValue(new ApiError(422, 'Schwelle abgelehnt.'));
	const support = within(card('Akku-Unterstützung'));
	await fireEvent.click(support.getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' }));
	expect(await support.findByText('Schwelle abgelehnt.')).toBeVisible();
});

it('Lesende sehen die Akku-Unterstützung ohne Bedienung', async () => {
	user.role = 'viewer';
	await show();
	const support = within(card('Akku-Unterstützung'));
	expect(support.queryByRole('checkbox')).toBeNull();
	expect(support.queryByRole('button')).toBeNull();
	expect(support.getByText('Wirksam ab 70 % (Konfiguration).')).toBeVisible();
});

it('ändert den Vorrang der Fahrzeuge', async () => {
	await show();
	api.setPriority.mockResolvedValue(reply());
	const priority = within(card('Vorrang'));
	expect(
		priority.getAllByRole('listitem').map((item) => item.querySelector('.name')?.textContent)
	).toEqual(['Tesla Model 3', 'e-Golf']);
	expect(priority.getByRole('button', { name: 'Tesla Model 3 nach oben' })).toBeDisabled();
	await fireEvent.click(priority.getByRole('button', { name: 'e-Golf nach oben' }));
	await waitFor(() =>
		expect(api.setPriority).toHaveBeenCalledWith(['egolf', 'tesla'], {
			kind: 'battery_until_soc',
			soc_pct: 30
		})
	);
});

it('wählt den Vorrang beim Überschuss', async () => {
	await show();
	api.setPriority.mockResolvedValue(reply());
	const select = within(card('Vorrang')).getByRole('combobox', { name: 'Vorrang beim Überschuss' });
	expect(select).toHaveValue('battery_until_soc');
	expect(
		within(select)
			.getAllByRole('option')
			.map((o) => o.textContent)
	).toEqual(['Autos zuerst', 'Akku zuerst', 'Akku bis 30\u00a0%, dann Autos']);
	await fireEvent.change(select, { target: { value: 'battery_first' } });
	await waitFor(() =>
		expect(api.setPriority).toHaveBeenCalledWith(['tesla', 'egolf'], {
			kind: 'battery_first',
			soc_pct: 30
		})
	);
});

it('ändert den SOC des Akku-Vorrangs', async () => {
	await show();
	api.setPriority.mockResolvedValue(reply());
	const input = within(card('Vorrang')).getByLabelText('Akku-SOC in %');
	expect(input).toHaveValue(30);
	await fireEvent.change(input, { target: { value: '45' } });
	await waitFor(() =>
		expect(api.setPriority).toHaveBeenCalledWith(['tesla', 'egolf'], {
			kind: 'battery_until_soc',
			soc_pct: 45
		})
	);
});

it('meldet, dass der core fehlt', async () => {
	api.system.mockResolvedValue({ ...system(), core: { connected: false, health: null } });
	api.control.mockResolvedValue({ core_connected: false, intent: null });
	api.limits.mockResolvedValue(limits());
	render(Page);
	expect(await screen.findByText('core nicht erreichbar.')).toBeInTheDocument();
	expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
});

it('folgt der Absicht, solange Health älter ist: Zuordnung bleibt auf dem gewählten Fahrzeug', async () => {
	await show();
	const pinned = baseIntent({
		version: 2,
		vehicles: { egolf: { mode: 'pv', boost_until: null, wallbox: 'twc' } }
	});
	api.setWallboxVehicle.mockResolvedValue(reply(pinned));
	api.control.mockResolvedValue({ core_connected: true, intent: pinned }); // Health hat Version 1
	const select = within(card('Garage')).getByRole('combobox', { name: 'Zuordnung' });
	await fireEvent.change(select, { target: { value: 'egolf' } });
	await waitFor(() => expect(api.setWallboxVehicle).toHaveBeenCalledWith('twc', 'egolf'));
	await waitFor(() => expect(select).toHaveValue('egolf'));
});

it('folgt der Absicht, solange Health älter ist: Boost zeigt „Boost beenden“', async () => {
	await show();
	const future = new Date(Date.now() + 6 * 3_600_000).toISOString();
	const boosted = baseIntent({
		version: 2,
		vehicles: { tesla: { mode: 'min_pv', boost_until: future, wallbox: null } }
	});
	api.setBoost.mockResolvedValue(reply(boosted));
	api.control.mockResolvedValue({ core_connected: true, intent: boosted }); // Health hat Version 1
	const tesla = within(card('Tesla Model 3'));
	await fireEvent.click(tesla.getByRole('button', { name: 'Boost' }));
	expect(await tesla.findByRole('button', { name: 'Boost beenden' })).toBeInTheDocument();
	expect(tesla.getByText(/^bis \d\d:\d\d$/)).toBeInTheDocument();
});
