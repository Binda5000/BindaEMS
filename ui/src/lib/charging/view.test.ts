import { describe, expect, it } from 'vitest';
import type { Intent, VehicleHealth } from '$lib/api/schemas';
import {
	assignmentLabel,
	boostInfo,
	healthIsCurrent,
	indicatorText,
	modeLabel,
	moveDown,
	moveUp,
	occupantText,
	overrideVehicle,
	priorityOrder,
	reasonLabel,
	surplusLabel
} from './view';

const names = { tesla: 'Tesla Model 3', egolf: 'e-Golf' };

function vehicle(changes: Partial<VehicleHealth> = {}): VehicleHealth {
	return {
		id: 'tesla',
		name: 'Tesla Model 3',
		mode: 'pv',
		boost_until: null,
		wallbox: 'twc',
		assignment: 'default',
		limit_a: null,
		plugged: null,
		charging: null,
		wakes_today: 0,
		commands_hour: null,
		boost_pending: false,
		override_pending: false,
		reason: null,
		...changes
	};
}

function intent(vehicles: Intent['vehicles'] = {}): Intent {
	return {
		version: 1,
		control: true,
		actuators: {},
		vehicles,
		priority: null,
		surplus_priority: { kind: 'cars_first', soc_pct: 30 },
		guest: { mode: 'pv' }
	};
}

describe('Texte', () => {
	it('benennt die Lademodi', () => {
		expect(
			(['pv', 'min_pv', 'now', 'off', 'boost'] as const).map((mode) => modeLabel(mode))
		).toEqual(['PV-Überschuss', 'Min+PV', 'Sofort', 'Aus', 'Boost']);
		expect(modeLabel(null)).toBe('–');
	});

	it('benennt die Art der Zuordnung', () => {
		expect(
			(['detected', 'default', 'override', 'unclear'] as const).map((kind) => assignmentLabel(kind))
		).toEqual(['erkannt', 'angenommen', 'Übersteuerung', 'unklar']);
		expect(assignmentLabel(null)).toBe('–');
	});

	it('beschreibt Indizien als Sätze', () => {
		expect(indicatorText({ vehicle: 'tesla', clue: 'current', supports: false }, names)).toBe(
			'Tesla Model 3: Strom passt nicht zu Tessie'
		);
		expect(indicatorText({ vehicle: 'egolf', clue: 'tessie', supports: true }, names)).toBe(
			'e-Golf: Tessie: zu Hause und angesteckt'
		);
		expect(indicatorText({ vehicle: 'x', clue: 'elsewhere', supports: false }, names)).toBe(
			'x: Andere Wallbox belegt'
		);
	});

	it('nennt alle Indizien in beiden Richtungen', () => {
		const clues = ['tessie', 'current', 'timing', 'phases', 'home', 'elsewhere'] as const;
		const text = (supports: boolean) =>
			clues.map((clue) => indicatorText({ vehicle: 'egolf', clue, supports }, names).slice(8));
		expect(text(true)).toEqual([
			'Tessie: zu Hause und angesteckt',
			'Strom passt zu Tessie',
			'Angesteckt, als der Vorgang begann',
			'Phasenzahl passt',
			'Home Assistant: zu Hause',
			'Angesteckt, andere Wallbox leer'
		]);
		expect(text(false)).toEqual([
			'Tessie: nicht angesteckt oder nicht zu Hause',
			'Strom passt nicht zu Tessie',
			'Angesteckt zu anderer Zeit',
			'Phasenzahl passt nicht',
			'Home Assistant: nicht zu Hause',
			'Andere Wallbox belegt'
		]);
	});

	it('nennt, wer an der Wallbox steht', () => {
		const base = { id: 'evcs', assignment: null, indicators: [] };
		expect(occupantText({ ...base, occupant: 'tesla', name: 'Tesla Model 3' }, names)).toBe(
			'Tesla Model 3'
		);
		expect(occupantText({ ...base, occupant: 'egolf', name: null }, names)).toBe('e-Golf');
		expect(occupantText({ ...base, occupant: 'guest', name: null }, names)).toBe('Gast');
		expect(occupantText({ ...base, occupant: null, name: null }, names)).toBe('kein Fahrzeug');
	});

	it('beschreibt den Vorrang beim Überschuss', () => {
		expect(surplusLabel({ kind: 'cars_first', soc_pct: 30 })).toBe('Autos zuerst');
		expect(surplusLabel({ kind: 'battery_first', soc_pct: 30 })).toBe('Akku zuerst');
		expect(surplusLabel({ kind: 'battery_until_soc', soc_pct: 30 })).toBe(
			'Akku bis 30\u00a0%, dann Autos'
		);
	});

	it('übersetzt den letzten Grund, Unbekanntes bleibt', () => {
		expect(reasonLabel('mode_min_pv')).toBe('Min+PV');
		expect(reasonLabel('self_start')).toBe('Selbststart');
		expect(reasonLabel('neu')).toBe('neu');
		expect(reasonLabel(null)).toBe('–');
	});
});

describe('Vorrang', () => {
	it('verschiebt nach oben', () => {
		expect(moveUp(['tesla', 'egolf'], 'egolf')).toEqual(['egolf', 'tesla']);
		expect(moveUp(['tesla', 'egolf'], 'tesla')).toEqual(['tesla', 'egolf']);
		expect(moveUp(['a', 'b', 'c'], 'c')).toEqual(['a', 'c', 'b']);
	});

	it('verschiebt nach unten', () => {
		expect(moveDown(['tesla', 'egolf'], 'tesla')).toEqual(['egolf', 'tesla']);
		expect(moveDown(['tesla', 'egolf'], 'egolf')).toEqual(['tesla', 'egolf']);
		expect(moveDown(['a', 'b', 'c'], 'a')).toEqual(['b', 'a', 'c']);
	});

	it('ändert die Eingabe nicht und kennt unbekannte Schlüssel nicht', () => {
		const order = ['a', 'b'];
		expect(moveUp(order, 'b')).not.toBe(order);
		expect(order).toEqual(['a', 'b']);
		expect(moveUp(order, 'x')).toEqual(['a', 'b']);
		expect(moveDown(order, 'x')).toEqual(['a', 'b']);
	});

	it('nimmt ohne Vorrang die Reihenfolge der Konfiguration und ergänzt Fehlende', () => {
		expect(priorityOrder(null, ['tesla', 'egolf'])).toEqual(['tesla', 'egolf']);
		expect(priorityOrder(['egolf'], ['tesla', 'egolf'])).toEqual(['egolf', 'tesla']);
		expect(priorityOrder(['alt', 'egolf', 'tesla'], ['tesla', 'egolf'])).toEqual([
			'egolf',
			'tesla'
		]);
	});
});

describe('Boost', () => {
	const now = Date.parse('2026-10-09T09:00:00Z');

	it('zeigt den Boost, den Health meldet', () => {
		expect(boostInfo(vehicle({ boost_until: '2026-10-09T12:00:00+00:00' }), intent(), now)).toEqual(
			{
				active: true,
				until: '2026-10-09T12:00:00+00:00',
				pending: false
			}
		);
	});

	it('zeigt einen wartenden Boost aus der Absicht', () => {
		const future = '2026-10-09T21:00:00+00:00';
		const vehicles = { tesla: { mode: 'pv' as const, boost_until: future, wallbox: null } };
		expect(boostInfo(vehicle({ boost_pending: true }), intent(vehicles), now)).toEqual({
			active: true,
			until: future,
			pending: true
		});
	});

	it('übergeht einen alten Boost der Absicht', () => {
		const past = '2026-10-09T08:00:00+00:00';
		const vehicles = { tesla: { mode: 'pv' as const, boost_until: past, wallbox: null } };
		expect(boostInfo(vehicle({ boost_pending: true }), intent(vehicles), now).active).toBe(false);
		const future = {
			tesla: { mode: 'pv' as const, boost_until: '2026-10-09T21:00:00Z', wallbox: null }
		};
		// ohne Wartezustand gilt der Boost der Absicht nicht (Health ist maßgeblich)
		expect(boostInfo(vehicle(), intent(future), now).active).toBe(false);
	});
});

describe('Übersteuerung', () => {
	const pinned = { egolf: { mode: 'pv' as const, boost_until: null, wallbox: 'twc' } };

	it('findet das Fahrzeug, das Health als Übersteuerung zeigt', () => {
		const vehicles = [
			vehicle({ id: 'tesla' }),
			vehicle({ id: 'egolf', wallbox: 'twc', assignment: 'override' })
		];
		expect(overrideVehicle('twc', vehicles, intent(pinned))).toBe('egolf');
		expect(overrideVehicle('evcs', vehicles, intent(pinned))).toBeNull();
	});

	it('übergeht eine beendete Übersteuerung der Absicht', () => {
		const vehicles = [vehicle({ id: 'egolf', wallbox: 'evcs', assignment: 'detected' })];
		expect(overrideVehicle('twc', vehicles, intent(pinned))).toBeNull();
	});

	it('zeigt eine wartende oder, ohne Stand der Regelung, die Übersteuerung der Absicht', () => {
		expect(
			overrideVehicle(
				'twc',
				[vehicle({ id: 'egolf', wallbox: null, override_pending: true })],
				intent(pinned)
			)
		).toBe('egolf');
		expect(
			overrideVehicle('twc', [vehicle({ id: 'egolf', mode: null, wallbox: null })], intent(pinned))
		).toBe('egolf');
	});
});

describe('Health älter als die Absicht', () => {
	const now = Date.parse('2026-10-09T09:00:00Z');
	const future = '2026-10-09T21:00:00+00:00';
	const boosted = intent({ tesla: { mode: 'pv', boost_until: future, wallbox: null } });
	const pinned = intent({ egolf: { mode: 'pv', boost_until: null, wallbox: 'twc' } });

	it('erkennt, ob Health die Absicht schon übernommen hat', () => {
		expect(healthIsCurrent(null, null)).toBe(true);
		expect(healthIsCurrent(null, intent())).toBe(false);
		expect(healthIsCurrent(0, intent())).toBe(false);
		expect(healthIsCurrent(1, intent())).toBe(true);
		expect(healthIsCurrent(2, intent())).toBe(true);
	});

	it('zeigt den Boost der Absicht, bis Health ihn bestätigt', () => {
		expect(boostInfo(vehicle(), boosted, now, false)).toEqual({
			active: true,
			until: future,
			pending: false
		});
		// mit aktuellem Health gilt, was Health zeigt
		expect(boostInfo(vehicle(), boosted, now, true).active).toBe(false);
	});

	it('zeigt einen beendeten Boost nicht mehr, auch wenn Health ihn noch zeigt', () => {
		const old = vehicle({ boost_until: '2026-10-09T12:00:00+00:00' });
		expect(boostInfo(old, intent(), now, false).active).toBe(false);
		expect(boostInfo(old, intent(), now, true).active).toBe(true);
	});

	it('übergeht in der Absicht einen Boost in der Vergangenheit', () => {
		const past = intent({
			tesla: { mode: 'pv', boost_until: '2026-10-09T08:00:00Z', wallbox: null }
		});
		expect(boostInfo(vehicle(), past, now, false).active).toBe(false);
	});

	it('zeigt die Übersteuerung der Absicht, bis Health sie bestätigt', () => {
		const vehicles = [vehicle({ id: 'egolf', wallbox: 'evcs', assignment: 'detected' })];
		expect(overrideVehicle('twc', vehicles, pinned, false)).toBe('egolf');
		expect(overrideVehicle('twc', vehicles, pinned, true)).toBeNull();
	});

	it('zeigt eine Übersteuerung nicht mehr, die die Absicht schon beendet hat', () => {
		const vehicles = [vehicle({ id: 'egolf', wallbox: 'twc', assignment: 'override' })];
		expect(overrideVehicle('twc', vehicles, intent(), false)).toBeNull();
	});
});
