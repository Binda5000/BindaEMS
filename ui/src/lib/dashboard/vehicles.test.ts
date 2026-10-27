import { describe, expect, it } from 'vitest';
import type { CoreState, VehicleHealth, WallboxHealth } from '$lib/api/schemas';
import { modeLabel } from '$lib/charging/view';
import { formatPower } from '$lib/format';
import { coreState, derived, limits, reading } from '$lib/testing/fixtures';
import { vehicleCards } from './vehicles';

const labels = { evcs: 'EVCS', twc: 'Garage' };

function vehicle(changes: Partial<VehicleHealth> = {}): VehicleHealth {
	return {
		id: 'tesla',
		name: 'Tesla Model 3',
		mode: 'min_pv',
		boost_until: null,
		wallbox: 'twc',
		assignment: 'detected',
		limit_a: null,
		plugged: true,
		charging: true,
		wakes_today: 0,
		commands_hour: null,
		boost_pending: false,
		override_pending: false,
		reason: null,
		soc_pct: null,
		soc_source: null,
		charge_limit_pct: null,
		planned: null,
		...changes
	};
}

function wallbox(id: string, occupant: string | null): WallboxHealth {
	return { id, occupant, name: null, assignment: null, indicators: [] };
}

function state(
	signals: CoreState['signals'] = {},
	wallboxW: Record<string, number | null> = { evcs: 0, twc: 1380 }
): CoreState {
	return { ...coreState(), signals, derived: derived({ wallbox_w: wallboxW }) };
}

const egolf = vehicle({
	id: 'egolf',
	name: 'e-Golf',
	mode: 'pv',
	wallbox: null,
	plugged: false,
	charging: false
});

describe('vehicleCards', () => {
	it('lädt: Wallbox, Leistung und Modus', () => {
		const health = { vehicles: [vehicle(), egolf], wallboxes: [wallbox('twc', 'tesla')] };
		const [tesla] = vehicleCards(
			state({ 'vehicle.tesla.soc_pct': reading(64) }),
			limits(),
			health,
			labels
		);
		expect(tesla).toEqual({
			id: 'tesla',
			name: 'Tesla Model 3',
			socPct: 64,
			socQuality: 'ok',
			guest: false,
			status: `Garage · lädt ${formatPower(1380)}`,
			mode: modeLabel('min_pv')
		});
		expect(tesla?.status).toMatch(/^Garage · lädt 1,38\u00a0kW$/);
		expect(tesla?.mode).toBe('Min+PV');
	});

	it('listet die Fahrzeuge in der Reihenfolge der Einstellungen; ohne Wallbox „nicht angesteckt“', () => {
		const cards = vehicleCards(
			state(),
			limits(),
			{ vehicles: [egolf, vehicle()], wallboxes: [] },
			labels
		);
		expect(cards.map((card) => card.id)).toEqual(['tesla', 'egolf']);
		expect(cards[1]).toMatchObject({
			name: 'e-Golf',
			socPct: null,
			socQuality: 'missing',
			status: 'nicht angesteckt',
			mode: modeLabel('pv')
		});
	});

	it('angesteckt ohne Laden', () => {
		const [tesla] = vehicleCards(
			state(),
			limits(),
			{ vehicles: [vehicle({ charging: false })], wallboxes: [] },
			labels
		);
		expect(tesla?.status).toBe('Garage · angesteckt');
	});

	it('lädt ohne bekannte Leistung: nur die Wallbox', () => {
		const [tesla] = vehicleCards(
			state({}, {}),
			limits(),
			{ vehicles: [vehicle()], wallboxes: [] },
			labels
		);
		expect(tesla?.status).toBe('Garage · lädt');
	});

	it('Wallbox ohne Namen zeigt den Schlüssel', () => {
		const [tesla] = vehicleCards(
			state(),
			limits(),
			{ vehicles: [vehicle({ charging: false })], wallboxes: [] },
			{}
		);
		expect(tesla?.status).toBe('twc · angesteckt');
	});

	it('veralteter Ladestand ist als solcher markiert', () => {
		const [tesla] = vehicleCards(
			state({ 'vehicle.tesla.soc_pct': reading(70, 'stale') }),
			limits(),
			{ vehicles: [vehicle()], wallboxes: [] },
			labels
		);
		expect(tesla).toMatchObject({ socPct: 70, socQuality: 'stale' });
	});

	it('ohne Gesundheitsbericht und Zustand: nicht angesteckt, ohne Modus', () => {
		const cards = vehicleCards(null, limits(), null, labels);
		expect(cards.map((card) => [card.status, card.mode, card.socQuality])).toEqual([
			['nicht angesteckt', null, 'missing'],
			['nicht angesteckt', null, 'missing']
		]);
	});

	it('ohne Einstellungen gibt es keine Karten', () => {
		expect(vehicleCards(state(), null, null, labels)).toEqual([]);
	});

	it('mit Gast: zusätzliche Karte für die Wallbox des Gastes', () => {
		const health = {
			vehicles: [vehicle({ wallbox: null, plugged: false, charging: false }), egolf],
			wallboxes: [wallbox('evcs', 'guest')]
		};
		const cards = vehicleCards(state({}, { evcs: 2300, twc: 0 }), limits(), health, labels);
		expect(cards).toHaveLength(3);
		expect(cards[2]).toEqual({
			id: 'guest:evcs',
			name: 'Gast',
			socPct: null,
			socQuality: 'missing',
			guest: true,
			status: `EVCS · lädt ${formatPower(2300)}`,
			mode: null
		});
	});

	it('Gast ohne Leistung ist angesteckt; Wallbox ohne Namen zeigt den Schlüssel', () => {
		const health = { vehicles: [], wallboxes: [wallbox('mystery', 'guest')] };
		const cards = vehicleCards(state({}, { mystery: 0 }), limits(), health, labels);
		expect(cards.at(-1)).toMatchObject({ guest: true, status: 'mystery · angesteckt' });
	});

	it('Wallboxen mit Fahrzeug oder ohne Vorgang erzeugen keine Gastkarte', () => {
		const health = {
			vehicles: [vehicle()],
			wallboxes: [wallbox('twc', 'tesla'), wallbox('evcs', null)]
		};
		expect(vehicleCards(state(), limits(), health, labels).some((card) => card.guest)).toBe(false);
	});
});
