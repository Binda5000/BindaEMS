import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChargingSession, Intent, PlanVehicle, VehicleHealth } from '$lib/api/schemas';
import { chargingSession } from '$lib/testing/fixtures';
import {
	assignmentLabel,
	boostInfo,
	CHARGE_MODES,
	GUEST_MODES,
	healthIsCurrent,
	indicatorText,
	latestSession,
	modeLabel,
	moveDown,
	moveUp,
	occupantText,
	overrideVehicle,
	parseSupportThreshold,
	parseTarget,
	planFits,
	plannedLine,
	priorityOrder,
	reasonLabel,
	sessionLine,
	supportHint,
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
		soc_pct: null,
		soc_source: null,
		charge_limit_pct: null,
		planned: null,
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
		guest: { mode: 'pv' },
		support: { allowed: true, soc_pct: null }
	};
}

describe('Texte', () => {
	it('benennt die Lademodi', () => {
		expect(
			(['pv', 'min_pv', 'now', 'off', 'planned', 'boost'] as const).map((mode) => modeLabel(mode))
		).toEqual(['PV-Überschuss', 'Min+PV', 'Sofort', 'Aus', 'Geplant', 'Boost']);
		expect(modeLabel(null)).toBe('–');
	});

	it('bietet „Geplant“ den Fahrzeugen an, einem Gast nicht', () => {
		expect(CHARGE_MODES).toEqual(['pv', 'min_pv', 'now', 'off', 'planned']);
		expect(GUEST_MODES).toEqual(['pv', 'min_pv', 'now', 'off']);
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
		expect(
			['mode_planned', 'plan_power', 'latest_start', 'target_reached', 'charge_limit'].map(
				reasonLabel
			)
		).toEqual(['Geplant', 'Plan: Laden', 'Späteste Startzeit', 'Ziel-SOC erreicht', 'Ladelimit']);
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
		const vehicles = {
			tesla: {
				mode: 'pv' as const,
				boost_until: future,
				wallbox: null,
				target_soc_pct: 80,
				departure: '07:00'
			}
		};
		expect(boostInfo(vehicle({ boost_pending: true }), intent(vehicles), now)).toEqual({
			active: true,
			until: future,
			pending: true
		});
	});

	it('übergeht einen alten Boost der Absicht', () => {
		const past = '2026-10-09T08:00:00+00:00';
		const vehicles = {
			tesla: {
				mode: 'pv' as const,
				boost_until: past,
				wallbox: null,
				target_soc_pct: 80,
				departure: '07:00'
			}
		};
		expect(boostInfo(vehicle({ boost_pending: true }), intent(vehicles), now).active).toBe(false);
		const future = {
			tesla: {
				mode: 'pv' as const,
				boost_until: '2026-10-09T21:00:00Z',
				wallbox: null,
				target_soc_pct: 80,
				departure: '07:00'
			}
		};
		// ohne Wartezustand gilt der Boost der Absicht nicht (Health ist maßgeblich)
		expect(boostInfo(vehicle(), intent(future), now).active).toBe(false);
	});
});

describe('Übersteuerung', () => {
	const pinned = {
		egolf: {
			mode: 'pv' as const,
			boost_until: null,
			wallbox: 'twc',
			target_soc_pct: 80,
			departure: '07:00'
		}
	};

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
	const boosted = intent({
		tesla: {
			mode: 'pv',
			boost_until: future,
			wallbox: null,
			target_soc_pct: 80,
			departure: '07:00'
		}
	});
	const pinned = intent({
		egolf: { mode: 'pv', boost_until: null, wallbox: 'twc', target_soc_pct: 80, departure: '07:00' }
	});

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
			tesla: {
				mode: 'pv',
				boost_until: '2026-10-09T08:00:00Z',
				wallbox: null,
				target_soc_pct: 80,
				departure: '07:00'
			}
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

describe('Akku-Unterstützung', () => {
	it('nennt Schwelle und Quelle', () => {
		expect(supportHint({ soc_pct: 82, source: 'plan' })).toBe('Wirksam ab 82\u00a0% (Plan).');
		expect(supportHint({ soc_pct: 70, source: 'config' })).toBe(
			'Wirksam ab 70\u00a0% (Konfiguration).'
		);
		expect(supportHint({ soc_pct: 60, source: 'intent' })).toBe(
			'Wirksam ab 60\u00a0% (Einstellung).'
		);
		expect(supportHint(null)).toBe('Wirksame Schwelle unbekannt.');
	});

	it('prüft die Eingabe der Schwelle gegen Reserve und 100 %', () => {
		expect(parseSupportThreshold('', 20)).toEqual({ ok: true, soc_pct: null });
		expect(parseSupportThreshold('  ', 20)).toEqual({ ok: true, soc_pct: null });
		expect(parseSupportThreshold('20', 20)).toEqual({ ok: true, soc_pct: 20 });
		expect(parseSupportThreshold('100', 20)).toEqual({ ok: true, soc_pct: 100 });
		expect(parseSupportThreshold('62.5', 20)).toEqual({ ok: true, soc_pct: 62.5 });
		expect(parseSupportThreshold('19', 20)).toEqual({
			ok: false,
			text: 'Unterstützungsschwelle „19“ liegt außerhalb von 20–100 %.'
		});
		expect(parseSupportThreshold('100.5', 20).ok).toBe(false);
		expect(parseSupportThreshold('abc', 20).ok).toBe(false);
		expect(parseSupportThreshold('', 20, true).ok).toBe(false);
	});
});

describe('Session in der Fahrzeugkarte', () => {
	const NB = '\u00a0';
	// 18:00–21:30 Wiener Zeit (MESZ)
	const done = (changes: Partial<ChargingSession> = {}) =>
		chargingSession({
			vehicle: 'tesla',
			open: false,
			start: '2026-10-08T16:00:00+00:00',
			end: '2026-10-08T19:30:00+00:00',
			kwh: 12.44,
			pv_kwh: 8,
			battery_kwh: 3,
			grid_kwh: 1.44,
			cost_eur: 0.52,
			...changes
		});
	const running = (changes: Partial<ChargingSession> = {}) =>
		done({
			open: true,
			end: null,
			start: '2026-10-09T06:45:00+00:00',
			kwh: 1.38,
			pv_kwh: 0.5,
			battery_kwh: 0.2,
			grid_kwh: 0.68,
			...changes
		});

	it('bevorzugt die offene Session', () => {
		const open = running({ id: 1 });
		const newer = done({ id: 2, start: '2026-10-09T05:00:00+00:00' });
		expect(latestSession([newer, open], 'tesla')).toBe(open);
		expect(latestSession([open, newer], 'tesla')).toBe(open);
	});

	it('nimmt sonst die jüngste beendete Session des Fahrzeugs', () => {
		const old = done({ id: 1, start: '2026-10-01T16:00:00+00:00' });
		const young = done({ id: 2 });
		const other = done({ id: 3, vehicle: 'egolf', start: '2026-10-09T16:00:00+00:00' });
		expect(latestSession([old, other, young], 'tesla')).toBe(young);
		expect(latestSession([other], 'tesla')).toBeNull();
		expect(latestSession([], 'tesla')).toBeNull();
	});

	it('beschreibt die laufende Session mit der Aufteilung', () => {
		expect(sessionLine(running())).toBe(
			`Session seit 08:45 · 1,4${NB}kWh (PV 0,5 · Akku 0,2 · Netz 0,7)`
		);
	});

	it('beschreibt die letzte Session mit Kosten, wenn sie bekannt sind', () => {
		expect(sessionLine(done())).toBe(
			`Letzte Session 08.10. 18:00–21:30 · 12,4${NB}kWh · 0,52${NB}€`
		);
	});

	it('schreibt die Kosten wie formatEur, mit Tausenderpunkt', () => {
		expect(sessionLine(done({ cost_eur: 1234.5 }))).toBe(
			`Letzte Session 08.10. 18:00–21:30 · 12,4${NB}kWh · 1.234,50${NB}€`
		);
	});

	it('lässt die Kosten weg, wenn sie unbekannt sind', () => {
		expect(sessionLine(done({ cost_eur: null }))).toBe(
			`Letzte Session 08.10. 18:00–21:30 · 12,4${NB}kWh`
		);
	});

	it('nennt das Datum des Endes, wenn die Session über Mitternacht ging', () => {
		expect(sessionLine(done({ end: '2026-10-08T23:15:00+00:00', cost_eur: null }))).toBe(
			`Letzte Session 08.10. 18:00–09.10. 01:15 · 12,4${NB}kWh`
		);
	});

	it('zeigt eine Session ohne Ende als laufend, auch wenn open fehlt', () => {
		expect(sessionLine(done({ end: null, open: false }))).toMatch(/^Session seit 18:00/);
	});
});

describe('Geplant', () => {
	const NB = '\u00a0';
	// Abfahrt 07:00, späteste Startzeit 01:30, fertig um 05:45 (Wiener Zeit, MESZ)
	const DEPARTURE = '2026-10-10T05:00:00+00:00';
	const planned = (changes: Partial<NonNullable<VehicleHealth['planned']>> = {}) => ({
		state: 'surplus' as const,
		target_soc_pct: 80,
		departure_at: DEPARTURE,
		need_kwh: 25,
		latest_start: '2026-10-09T23:30:00+00:00',
		plan_w: null,
		...changes
	});
	const car = (changes: Partial<PlanVehicle> = {}): PlanVehicle => ({
		key: 'tesla',
		name: 'Tesla Model 3',
		wallbox: 'twc',
		target_soc_pct: 80,
		departure_at: DEPARTURE,
		soc_start_pct: 50,
		soc_assumed: false,
		limit_pct: null,
		need_kwh: 25,
		planned_kwh: 22.5,
		shortfall_kwh: 0,
		expected_soc_pct: 80,
		finish_at: '2026-10-10T03:45:00+00:00',
		reachable: true,
		expected: false,
		arrival_at: null,
		...changes
	});
	const health = (changes: Partial<VehicleHealth> = {}) =>
		vehicle({
			mode: 'planned',
			plugged: true,
			soc_pct: 50,
			soc_source: 'measured',
			planned: planned(),
			...changes
		});

	it('nennt mit Plan, wann das Auto fertig ist', () => {
		expect(plannedLine({ mode: 'planned', vehicle: health(), car: car(), target: 80 })).toEqual({
			text: `Fertig um 05:45 · Ziel 80${NB}% bis 07:00`,
			notes: []
		});
	});

	it('sagt, wenn der Plan das Ziel nicht erreicht', () => {
		const late = car({ reachable: false, expected_soc_pct: 61.6, shortfall_kwh: 8 });
		expect(plannedLine({ mode: 'planned', vehicle: health(), car: late, target: 80 })?.text).toBe(
			`Ziel 80${NB}% bis 07:00 nicht erreichbar, voraussichtlich 62${NB}%.`
		);
	});

	it('nennt ohne Plan die späteste Startzeit aus Health', () => {
		expect(plannedLine({ mode: 'planned', vehicle: health(), car: null, target: 80 })?.text).toBe(
			'Kein Plan · lädt spätestens ab 01:30'
		);
	});

	it('meldet das erreichte Ziel aus Health vor dem Plan', () => {
		const done = health({ soc_pct: 80.4, planned: planned({ state: 'reached', need_kwh: 0 }) });
		expect(plannedLine({ mode: 'planned', vehicle: done, car: car(), target: 80 })?.text).toBe(
			`Ziel erreicht (80${NB}%).`
		);
	});

	it('meldet ein Ziel nie als erreicht, wenn der Ladestand angenommen ist', () => {
		const assumed = health({
			soc_pct: 0,
			soc_source: 'assumed',
			planned: planned({ state: 'reached' })
		});
		expect(plannedLine({ mode: 'planned', vehicle: assumed, car: null, target: 80 })?.text).toBe(
			'Kein Plan · lädt spätestens ab 01:30'
		);
	});

	it('meldet ein Ziel ohne Bedarf im Plan als erreicht, wenn der Ladestand bekannt ist', () => {
		const full = car({ soc_start_pct: 85, need_kwh: 0, planned_kwh: 0, finish_at: null });
		expect(plannedLine({ mode: 'planned', vehicle: null, car: full, target: 80 })?.text).toBe(
			`Ziel erreicht (85${NB}%).`
		);
		// ohne Laden im Horizont und ohne bekanntes Ziel sagt der Plan nichts über „Fertig um“
		const later = car({ planned_kwh: 0, finish_at: null });
		expect(plannedLine({ mode: 'planned', vehicle: health(), car: later, target: 80 })?.text).toBe(
			null
		);
	});

	it('nennt die Quelle des Ladestands, angenommen nie als gemessen', () => {
		const estimated = health({ soc_source: 'estimated' });
		expect(
			plannedLine({ mode: 'planned', vehicle: estimated, car: car(), target: 80 })?.notes
		).toEqual(['Ladestand geschätzt']);
		const assumed = health({ soc_pct: 0, soc_source: 'assumed' });
		expect(
			plannedLine({ mode: 'planned', vehicle: assumed, car: car(), target: 80 })?.notes
		).toEqual([`Ladestand unbekannt, gerechnet ab 0${NB}%.`]);
		// ohne Health gilt der Plan: ein angenommener Ladestand bleibt angenommen
		const planOnly = car({ soc_start_pct: 0, soc_assumed: true });
		expect(plannedLine({ mode: null, vehicle: null, car: planOnly, target: null })?.notes).toEqual([
			`Ladestand unbekannt, gerechnet ab 0${NB}%.`
		]);
		expect(
			plannedLine({ mode: 'planned', vehicle: health(), car: car(), target: 80 })?.notes
		).toEqual([]);
	});

	it('sagt, wenn das Ladelimit im Auto unter dem Ziel liegt', () => {
		const limited = health({ charge_limit_pct: 70, planned: planned({ target_soc_pct: 70 }) });
		const line = plannedLine({
			mode: 'planned',
			vehicle: limited,
			car: car({ target_soc_pct: 70, limit_pct: 70 }),
			target: 80
		});
		expect(line?.text).toBe(`Fertig um 05:45 · Ziel 70${NB}% bis 07:00`);
		expect(line?.notes).toEqual([`Ladelimit im Auto 70${NB}% liegt unter dem Ziel.`]);
		// gleicht der core das Limit an (das wirksame Ziel bleibt), gibt es den Satz nicht
		const synced = health({ charge_limit_pct: 70 });
		expect(
			plannedLine({ mode: 'planned', vehicle: synced, car: car(), target: 80 })?.notes
		).toEqual([]);
	});

	it('zeigt nichts außerhalb von „Geplant“ und für ein abgestecktes Auto', () => {
		expect(plannedLine({ mode: 'pv', vehicle: health(), car: car(), target: 80 })).toBeNull();
		expect(
			plannedLine({ mode: 'planned', vehicle: health({ plugged: false }), car: car(), target: 80 })
		).toBeNull();
		expect(
			plannedLine({ mode: 'planned', vehicle: health({ planned: null }), car: null, target: 80 })
		).toBeNull();
	});

	it('vergleicht das Auto im Plan mit Ziel und Abfahrt in Health', () => {
		expect(planFits(health(), car())).toBe(true);
		expect(planFits(health(), car({ target_soc_pct: 80.04 }))).toBe(true);
		expect(planFits(health(), car({ departure_at: '2026-10-10T07:00:00+02:00' }))).toBe(true);
		expect(planFits(health(), car({ target_soc_pct: 60 }))).toBe(false);
		expect(planFits(health(), car({ departure_at: '2026-10-10T05:30:00+00:00' }))).toBe(false);
		expect(planFits(health({ planned: null }), car({ target_soc_pct: 60 }))).toBe(true);
		expect(planFits(null, car())).toBe(true);
	});

	describe('Abfahrt an einem Tag (Ruling F-3)', () => {
		// Freitag, 09.10.2026, 20:00 in Wien; Abfahrt Montag 07:00
		const monday = '2026-10-12T05:00:00+00:00';
		const dated = car({ departure_at: monday, finish_at: '2026-10-12T03:45:00+00:00' });
		const mondayHealth = () => health({ planned: planned({ departure_at: monday }) });

		beforeEach(() => {
			vi.useFakeTimers();
			vi.setSystemTime(new Date('2026-10-09T18:00:00Z'));
		});
		afterEach(() => vi.useRealTimers());

		it('nennt den Tag, wenn die Absicht einen nennt', () => {
			const line = plannedLine({
				mode: 'planned',
				vehicle: mondayHealth(),
				car: dated,
				target: 80,
				departureOn: '2026-10-12'
			});
			expect(line?.text).toBe(`Fertig um 05:45 · Ziel 80${NB}% bis Mo 12.10. 07:00`);
			const short = car({ departure_at: monday, reachable: false, expected_soc_pct: 60 });
			expect(
				plannedLine({
					mode: 'planned',
					vehicle: mondayHealth(),
					car: short,
					target: 80,
					departureOn: '2026-10-12'
				})?.text
			).toBe(`Ziel 80${NB}% bis Mo 12.10. 07:00 nicht erreichbar, voraussichtlich 60${NB}%.`);
		});

		it('nennt ihn im Zeitplan ohne Absicht ab übermorgen', () => {
			expect(plannedLine({ mode: null, vehicle: null, car: dated, target: null })?.text).toBe(
				`Fertig um 05:45 · Ziel 80${NB}% bis Mo 12.10. 07:00`
			);
			const tomorrow = car({ departure_at: '2026-10-10T05:00:00+00:00' });
			expect(plannedLine({ mode: null, vehicle: null, car: tomorrow, target: null })?.text).toBe(
				`Fertig um 05:45 · Ziel 80${NB}% bis 07:00`
			);
		});
	});

	it('nennt bei unbekanntem Plan weder Plan noch „Kein Plan“', () => {
		expect(plannedLine({ mode: 'planned', vehicle: health(), car: undefined, target: 80 })).toEqual(
			{ text: null, notes: [] }
		);
	});

	it('prüft Ziel-SOC und Abfahrt und schickt nur, was sich ändert', () => {
		const now = { target_soc_pct: 60, departure: '17:30' };
		expect(parseTarget('80', '07:00', now)).toEqual({
			ok: true,
			target_soc_pct: 80,
			departure: '07:00'
		});
		expect(parseTarget('60', '07:00', now)).toEqual({
			ok: true,
			target_soc_pct: null,
			departure: '07:00'
		});
		expect(parseTarget('60', '17:30', now)).toEqual({
			ok: true,
			target_soc_pct: null,
			departure: null
		});
		expect(parseTarget('19', '07:00', now)).toEqual({
			ok: false,
			text: 'Ziel-SOC „19“ liegt außerhalb von 20–100 %.'
		});
		expect(parseTarget('80.5', '07:00', now).ok).toBe(false);
		expect(parseTarget('', '07:00', now).ok).toBe(false);
		expect(parseTarget('80', '07:00', now, true).ok).toBe(false);
		expect(parseTarget('80', '24:00', now)).toEqual({
			ok: false,
			text: 'Abfahrt „24:00“ ist keine Uhrzeit (HH:MM).'
		});
		expect(parseTarget('80', '', now).ok).toBe(false);
	});
});
