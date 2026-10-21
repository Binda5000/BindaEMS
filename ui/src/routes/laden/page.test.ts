import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import * as v from 'valibot';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import boostCostJson from '$lib/api/contract/boost-cost.json';
import controlJson from '$lib/api/contract/control.json';
import { ApiError } from '$lib/api/errors';
import {
	ControlResponseSchema,
	SystemResponseSchema,
	type BoostCost,
	type ChargingSession,
	type ControlReply,
	type CoreHealth,
	type Intent,
	type PlanResponse,
	type Role,
	type VehicleHealth,
	type WallboxHealth
} from '$lib/api/schemas';
import { chargingSession, driving, limits, system } from '$lib/testing/fixtures';
import { RESPONSE as PLAN_RESPONSE } from '$lib/testing/plan';
import Page from './+page.svelte';

const { api, user } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		control: vi.fn(),
		limits: vi.fn(),
		boostCost: vi.fn(),
		chargingSessions: vi.fn(),
		plan: vi.fn(),
		setRegulation: vi.fn(),
		setTarget: vi.fn(),
		setMode: vi.fn(),
		setBoost: vi.fn(),
		setWallboxVehicle: vi.fn(),
		setGuestMode: vi.fn(),
		setSupport: vi.fn(),
		setPriority: vi.fn(),
		drivingStatus: vi.fn(),
		setAutomation: vi.fn()
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
async function show(
	response = system(),
	intent: Intent | null = baseIntent(),
	boostCost: BoostCost = boostCostJson,
	sessions: ChargingSession[] = [],
	plan: PlanResponse = PLAN_RESPONSE
) {
	api.system.mockResolvedValue(response);
	api.plan.mockResolvedValue(plan);
	api.boostCost.mockResolvedValue(boostCost);
	api.control.mockResolvedValue({ core_connected: true, intent });
	api.limits.mockResolvedValue(limits());
	api.chargingSessions.mockResolvedValue(sessions);
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
	expect(select).toHaveValue('planned');
	expect(
		within(select)
			.getAllByRole('option')
			.map((o) => o.textContent)
	).toEqual(['PV-Überschuss', 'Min+PV', 'Sofort', 'Aus', 'Geplant']);
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

it('Bedienende sehen die Mehrkosten des Boosts an der Fahrzeugkarte', async () => {
	await show();
	const tesla = within(card('Tesla Model 3'));
	expect(
		await tesla.findByText(
			'Boost kann bis zu 66,24 € Leistungspreis im Monat kosten (11,0 kW mehr Spitze).'
		)
	).toBeVisible();
	expect(
		within(card('e-Golf')).getByText(
			'Boost kann bis zu 44,16 € Leistungspreis im Monat kosten (7,4 kW mehr Spitze).'
		)
	).toBeVisible();
});

it('Lesende sehen die Mehrkosten nicht', async () => {
	user.role = 'viewer';
	await show();
	await waitFor(() => expect(api.boostCost).toHaveBeenCalled());
	expect(screen.queryByText(/Leistungspreis im Monat/)).not.toBeInTheDocument();
});

it('ohne Leistungspreis mit Satz fehlt der Satz zu den Mehrkosten', async () => {
	await show(system(), baseIntent(), { applies: false, month_peak_kw: 5, vehicles: [] });
	await waitFor(() => expect(api.boostCost).toHaveBeenCalled());
	expect(screen.queryByText(/Leistungspreis im Monat/)).not.toBeInTheDocument();
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
		vehicles: {
			tesla: {
				mode: 'min_pv',
				boost_until: future,
				wallbox: null,
				target_soc_pct: 80,
				departure: '07:00'
			}
		}
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

it('nennt den Tag einer Abfahrt an einem Tag in der Zeile und neben dem Feld', async () => {
	// Ruling F-3: Donnerstag, 08.10.; die Absicht nennt Freitag, 09.10., 17:30
	vi.useFakeTimers({ toFake: ['Date'] });
	vi.setSystemTime(new Date('2026-10-08T10:00:00Z'));
	const base = baseIntent();
	const dated = {
		...base,
		vehicles: {
			...base.vehicles,
			tesla: { ...base.vehicles.tesla!, departure_on: '2026-10-09' }
		}
	};
	await show(system(), dated);
	const tesla = card('Tesla Model 3');
	expect(
		await within(tesla).findByText('Fertig um 16:30 · Ziel 60 % bis Fr 09.10. 17:30')
	).toBeInTheDocument();
	const manual = within(tesla).getByRole('group', { name: 'Von Hand' });
	expect(within(manual).getByText('am Fr 09.10.')).toBeInTheDocument();
});

it('zeigt eine Wallbox ohne Vorgang', async () => {
	const health = system().core.health!;
	await show(healthWith({ wallboxes: wallboxAt(health, 'evcs', { occupant: null }) }));
	expect(within(card('EVCS')).getByText('kein Fahrzeug')).toBeInTheDocument();
});

// Demo: Der Tesla lädt „Geplant“ (Ziel 60 %, Abfahrt 17:30), der Plan lädt ihn bis 16:30; sein
// Ladestand ist angenommen. Der e-Golf hat einen geschätzten Ladestand.
it('zeigt „Geplant“ mit dem Plan, der Quelle des Ladestands und den Feldern', async () => {
	await show();
	const tesla = card('Tesla Model 3');
	// testing-library vergleicht mit Leerzeichen statt geschützter Leerzeichen
	expect(
		await within(tesla).findByText('Fertig um 16:30 · Ziel 60 % bis 17:30')
	).toBeInTheDocument();
	expect(within(tesla).getByText('Ladestand unbekannt, gerechnet ab 0 %.')).toBeInTheDocument();
	expect(within(tesla).getByRole('spinbutton', { name: 'Ziel-SOC' })).toHaveValue(60);
	expect(within(tesla).getByLabelText('Abfahrt')).toHaveValue('17:30');
	expect(within(card('e-Golf')).getByText('geschätzt').parentElement).toHaveTextContent(
		'49 % geschätzt'
	);
	expect(within(card('e-Golf')).queryByRole('spinbutton', { name: 'Ziel-SOC' })).toBeNull();
});

it('nennt ohne gültigen Plan die späteste Startzeit aus Health', async () => {
	await show(system(), baseIntent(), boostCostJson, [], {
		...PLAN_RESPONSE,
		plan: { ...PLAN_RESPONSE.plan!, valid: false }
	});
	expect(
		await within(card('Tesla Model 3')).findByText('Kein Plan · lädt spätestens ab 12:16')
	).toBeInTheDocument();
});

it('Bedienen übernimmt Ziel-SOC und Abfahrt', async () => {
	await show();
	api.setTarget.mockResolvedValue(reply());
	const tesla = card('Tesla Model 3');
	await fireEvent.input(within(tesla).getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '80' }
	});
	await fireEvent.input(within(tesla).getByLabelText('Abfahrt'), { target: { value: '07:00' } });
	await fireEvent.click(within(tesla).getByRole('button', { name: 'Übernehmen' }));
	await waitFor(() => expect(api.setTarget).toHaveBeenCalledWith('tesla', 80, '07:00'));
});

it('nach Übernehmen holt die Seite den Plan neu', async () => {
	// Der neue Plan kommt erst nach dem nächsten Health; die Zeile „Fertig um …“ soll nicht bis
	// zur nächsten Minute das alte Ziel zeigen.
	await show();
	api.setTarget.mockResolvedValue(reply());
	vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
	const loaded = api.plan.mock.calls.length;
	const tesla = card('Tesla Model 3');
	await fireEvent.input(within(tesla).getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '80' }
	});
	await fireEvent.click(within(tesla).getByRole('button', { name: 'Übernehmen' }));
	await vi.advanceTimersByTimeAsync(1900);
	expect(api.plan).toHaveBeenCalledTimes(loaded);
	await vi.advanceTimersByTimeAsync(200);
	expect(api.plan).toHaveBeenCalledTimes(loaded + 1);
});

it('zeigt „Fertig um“ erst wieder, wenn der Plan zu Ziel und Abfahrt in Health passt', async () => {
	// Health meldet nach „Übernehmen“ das neue Ziel 80 %, der Plan rechnet noch mit 60 %: Die
	// Zeile „Fertig um …“ fehlt, bis die app neu geplant hat; die Seite fragt höchstens alle 10 s.
	await show();
	api.setTarget.mockResolvedValue(reply());
	vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
	const health = system().core.health!;
	const before = health.vehicles.find((vehicle) => vehicle.id === 'tesla')!.planned!;
	api.system.mockResolvedValue(
		healthWith({
			vehicles: vehicleAt(health, 'tesla', { planned: { ...before, target_soc_pct: 80 } })
		})
	);
	const tesla = card('Tesla Model 3');
	await fireEvent.input(within(tesla).getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '80' }
	});
	await fireEvent.click(within(tesla).getByRole('button', { name: 'Übernehmen' }));
	await vi.advanceTimersByTimeAsync(2100);
	expect(within(tesla).queryByText(/Fertig um/)).toBeNull();
	expect(within(tesla).getByText('Ladestand unbekannt, gerechnet ab 0 %.')).toBeInTheDocument();
	const loaded = api.plan.mock.calls.length;
	await vi.advanceTimersByTimeAsync(9800);
	expect(api.plan).toHaveBeenCalledTimes(loaded);
	await vi.advanceTimersByTimeAsync(300);
	expect(api.plan).toHaveBeenCalledTimes(loaded + 1);
	const [car] = PLAN_RESPONSE.plan!.vehicles;
	api.plan.mockResolvedValue({
		...PLAN_RESPONSE,
		plan: { ...PLAN_RESPONSE.plan!, vehicles: [{ ...car, target_soc_pct: 80 }] }
	});
	await vi.advanceTimersByTimeAsync(10_100);
	expect(api.plan).toHaveBeenCalledTimes(loaded + 2);
	expect(within(tesla).getByText('Fertig um 16:30 · Ziel 80 % bis 17:30')).toBeInTheDocument();
	await vi.advanceTimersByTimeAsync(30_000);
	expect(api.plan).toHaveBeenCalledTimes(loaded + 2);
});

it('zeigt die Ablehnung des Ziels an der Karte', async () => {
	await show();
	api.setTarget.mockRejectedValue(
		new ApiError(422, 'Ziel-SOC „101“ liegt außerhalb von 20–100 %.')
	);
	const tesla = card('Tesla Model 3');
	await fireEvent.input(within(tesla).getByLabelText('Abfahrt'), { target: { value: '07:00' } });
	await fireEvent.click(within(tesla).getByRole('button', { name: 'Übernehmen' }));
	expect(
		await within(tesla).findByText('Ziel-SOC „101“ liegt außerhalb von 20–100 %.')
	).toBeInTheDocument();
});

it('zeigt die Übersteuerung einer Wallbox in der Auswahl', async () => {
	const health = system().core.health!;
	const intent = baseIntent({
		vehicles: {
			egolf: {
				mode: 'pv',
				boost_until: null,
				wallbox: 'twc',
				target_soc_pct: 80,
				departure: '07:00'
			}
		}
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
	// Ein Gast kann nicht „Geplant“ laden
	expect(
		within(select)
			.getAllByRole('option')
			.map((o) => o.textContent)
	).toEqual(['PV-Überschuss', 'Min+PV', 'Sofort', 'Aus']);
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
		vehicles: {
			egolf: {
				mode: 'pv',
				boost_until: null,
				wallbox: 'twc',
				target_soc_pct: 80,
				departure: '07:00'
			}
		}
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
		vehicles: {
			tesla: {
				mode: 'min_pv',
				boost_until: future,
				wallbox: null,
				target_soc_pct: 80,
				departure: '07:00'
			}
		}
	});
	api.setBoost.mockResolvedValue(reply(boosted));
	api.control.mockResolvedValue({ core_connected: true, intent: boosted }); // Health hat Version 1
	const tesla = within(card('Tesla Model 3'));
	await fireEvent.click(tesla.getByRole('button', { name: 'Boost' }));
	expect(await tesla.findByRole('button', { name: 'Boost beenden' })).toBeInTheDocument();
	expect(tesla.getByText(/^bis \d\d:\d\d$/)).toBeInTheDocument();
});

it('zeigt die laufende Session in der Karte des Fahrzeugs', async () => {
	const running = chargingSession({
		vehicle: 'tesla',
		open: true,
		end: null,
		start: '2026-10-09T06:45:00+00:00',
		kwh: 1.38,
		pv_kwh: 0,
		battery_kwh: 0,
		grid_kwh: 1.38
	});
	const other = chargingSession({ vehicle: 'egolf', open: false });
	await show(system(), baseIntent(), boostCostJson, [other, running]);
	await within(card('Tesla Model 3')).findByText(/^Session seit 08:45 · 1,4 kWh/);
	expect(within(card('e-Golf')).getByText(/^Letzte Session /)).toBeVisible();
});

it('lädt die Sessions der letzten 30 Tage', async () => {
	vi.useFakeTimers({ toFake: ['Date'] });
	vi.setSystemTime(new Date('2026-10-17T10:00:00Z'));
	await show();
	await waitFor(() => expect(api.chargingSessions).toHaveBeenCalled());
	expect(api.chargingSessions).toHaveBeenCalledWith(
		'2026-09-17',
		'2026-10-17',
		undefined,
		expect.anything()
	);
});

it('lädt die Sessions jede Minute und rückt das 30-Tage-Fenster über Mitternacht nach', async () => {
	vi.useFakeTimers({
		toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout']
	});
	vi.setSystemTime(new Date('2026-10-17T21:59:30Z')); // 23:59:30 in Wien
	await show();
	await vi.advanceTimersByTimeAsync(0);
	expect(api.chargingSessions).toHaveBeenCalledTimes(1);
	expect(api.chargingSessions.mock.lastCall?.slice(0, 2)).toEqual(['2026-09-17', '2026-10-17']);
	await vi.advanceTimersByTimeAsync(59_000);
	expect(api.chargingSessions).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(1_000); // 00:00:30 am 18.10.
	expect(api.chargingSessions).toHaveBeenCalledTimes(2);
	expect(api.chargingSessions.mock.lastCall?.slice(0, 2)).toEqual(['2026-09-18', '2026-10-18']);
	await vi.advanceTimersByTimeAsync(60_000);
	expect(api.chargingSessions).toHaveBeenCalledTimes(3);
});

it('zeigt ohne Session keine Zeile', async () => {
	await show();
	await waitFor(() => expect(api.chargingSessions).toHaveBeenCalled());
	expect(screen.queryByText(/Session/)).not.toBeInTheDocument();
});

it('lässt die Karten heil, wenn die Abfrage der Sessions fehlschlägt', async () => {
	api.chargingSessions.mockRejectedValue(new ApiError(500, 'kaputt'));
	api.system.mockResolvedValue(system());
	api.boostCost.mockResolvedValue(boostCostJson);
	api.control.mockResolvedValue({ core_connected: true, intent: baseIntent() });
	api.limits.mockResolvedValue(limits());
	render(Page);
	await screen.findByRole('heading', { level: 2, name: 'Tesla Model 3' });
	await waitFor(() => expect(api.chargingSessions).toHaveBeenCalled());
	expect(screen.queryByText(/Session/)).not.toBeInTheDocument();
	expect(screen.queryByText('kaputt')).not.toBeInTheDocument();
});

describe('Fahrverhalten an den Fahrzeugkarten', () => {
	const suggestion = (name: string) =>
		within(within(card(name)).getByRole('group', { name: 'Vorschlag' }));

	it('zeigt Vorschlag und Zustand der Automatik je Fahrzeug', async () => {
		api.drivingStatus.mockResolvedValue(driving());
		await show();
		await waitFor(() =>
			expect(
				suggestion('Tesla Model 3').getByText('Vorschlag: Mo 07:15 · 50 % (Konfidenz hoch)')
			).toBeVisible()
		);
		expect(
			within(card('Tesla Model 3')).getByText(
				'Automatik pausiert bis Fr 17:30, weil Ziel oder Abfahrt von Hand geändert wurden.'
			)
		).toBeVisible();
		expect(
			suggestion('e-Golf').getByText('Vorschlag: Mo 08:00 · 50 % (Konfidenz niedrig)')
		).toBeVisible();
		expect(within(card('e-Golf')).getByText('Automatik aus.')).toBeVisible();
	});

	it('Übernehmen sendet Ziel, Abfahrt und Abfahrtstag und lädt das Fahrverhalten neu', async () => {
		api.drivingStatus.mockResolvedValue(driving());
		await show();
		api.setTarget.mockResolvedValue(reply());
		await waitFor(() => expect(suggestion('e-Golf').getByRole('button')).toBeEnabled());
		const loaded = api.drivingStatus.mock.calls.length;
		await fireEvent.click(suggestion('e-Golf').getByRole('button', { name: 'Übernehmen' }));
		await waitFor(() =>
			expect(api.setTarget).toHaveBeenCalledWith('egolf', 50, '08:00', '2026-10-12')
		);
		await waitFor(() => expect(api.drivingStatus.mock.calls.length).toBeGreaterThan(loaded));
	});

	it('Übernehmen am Tesla in „Geplant“ ist vom Übernehmen der Felder getrennt', async () => {
		api.drivingStatus.mockResolvedValue(driving());
		await show();
		api.setTarget.mockResolvedValue(reply());
		await waitFor(() => expect(suggestion('Tesla Model 3').getByRole('button')).toBeEnabled());
		await fireEvent.click(suggestion('Tesla Model 3').getByRole('button', { name: 'Übernehmen' }));
		await waitFor(() =>
			expect(api.setTarget).toHaveBeenCalledWith('tesla', 50, '07:15', '2026-10-12')
		);
	});

	it('Bedienen schaltet die Automatik und sieht danach den neuen Zustand', async () => {
		api.drivingStatus.mockResolvedValue(driving());
		await show();
		const egolf = driving().vehicles[1]!;
		api.setAutomation.mockResolvedValue({ ...egolf.automation, enabled: true });
		const toggle = await within(card('e-Golf')).findByRole('switch', {
			name: 'Ziel und Abfahrt automatisch'
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		api.drivingStatus.mockResolvedValue(
			driving({
				vehicles: [
					driving().vehicles[0]!,
					{
						...egolf,
						automation: {
							...egolf.automation,
							enabled: true,
							state: 'inactive',
							text: 'Automatik wartet: Das Auto steht nicht auf „Geplant“.'
						}
					}
				]
			})
		);
		await fireEvent.click(toggle);
		await waitFor(() => expect(api.setAutomation).toHaveBeenCalledWith('egolf', true));
		expect(
			await within(card('e-Golf')).findByText(
				'Automatik wartet: Das Auto steht nicht auf „Geplant“.'
			)
		).toBeVisible();
	});

	it('Lesende sehen den Schalter gesperrt und keine Taste', async () => {
		user.role = 'viewer';
		api.drivingStatus.mockResolvedValue(driving());
		await show();
		const toggle = await within(card('e-Golf')).findByRole('switch', {
			name: 'Ziel und Abfahrt automatisch'
		});
		expect(toggle).toBeDisabled();
		expect(within(card('e-Golf')).queryByRole('button', { name: 'Übernehmen' })).toBeNull();
		expect(within(card('e-Golf')).getByText('Automatik aus.')).toBeVisible();
	});

	it('zeigt die Ablehnung der Automatik an der Karte', async () => {
		api.drivingStatus.mockResolvedValue(driving());
		await show();
		api.setAutomation.mockRejectedValue(new ApiError(409, 'core nicht erreichbar'));
		const toggle = await within(card('e-Golf')).findByRole('switch', {
			name: 'Ziel und Abfahrt automatisch'
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		await fireEvent.click(toggle);
		expect(await within(card('e-Golf')).findByText('core nicht erreichbar')).toBeVisible();
		expect(toggle).not.toBeChecked();
	});

	it('zeigt ohne Antwort zum Fahrverhalten keine Vorschläge', async () => {
		api.drivingStatus.mockRejectedValue(new ApiError(500, 'kaputt'));
		await show();
		expect(within(card('Tesla Model 3')).queryByText(/Vorschlag/)).toBeNull();
		expect(within(card('Tesla Model 3')).queryByRole('switch')).toBeNull();
	});
});
