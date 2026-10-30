import { describe, expect, it } from 'vitest';
import type { CoreState, PlanResponse, PlanSlot } from '$lib/api/schemas';
import { formatPower, formatSoc } from '$lib/format';
import { parseIso } from '$lib/time';
import { coreState, derived, limits, reading } from '$lib/testing/fixtures';
import { RESPONSE, iso, slot } from '$lib/testing/plan';
import {
	batteryLabel,
	batteryView,
	energyText,
	gridChargeText,
	outlookText,
	planOutlook,
	powerText,
	reserveEnergyText,
	type BatteryView
} from './battery';

const NOW = parseIso(iso('12:00')) as number;

function stateWith(
	signals: Record<string, ReturnType<typeof reading>>,
	batteryW: number | null = 1000
): CoreState {
	return { ...coreState(), signals, derived: derived({ battery_w: batteryW }) };
}

function socState(soc: ReturnType<typeof reading>, batteryW: number | null = 1000): CoreState {
	return stateWith({ 'battery.soc_pct': soc }, batteryW);
}

/** Viertelstunden ab `from` (lokal) mit den geplanten Ladeständen am Ende des Slots */
function slotsFrom(from: string, socs: number[]): PlanSlot[] {
	const [hour, minute] = from.split(':').map(Number);
	return socs.map((soc, index) => {
		const total = (hour ?? 0) * 60 + (minute ?? 0) + index * 15;
		const local = `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
		return slot(local, 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH', { soc_pct: soc });
	});
}

function planResponse(overrides: Partial<PlanResponse['plan'] & object> | null): PlanResponse {
	const base = RESPONSE.plan;
	if (!base) throw new Error('plan.json enthält keinen Plan');
	return { ...RESPONSE, plan: overrides === null ? null : { ...base, ...overrides } };
}

/** Ende des Slots, der lokal um `local` beginnt */
function endOf(local: string): string {
	return new Date((parseIso(iso(local)) as number) + 15 * 60_000).toISOString();
}

describe('batteryView', () => {
	it('rechnet SOC 55 und 30 kWh in kWh um', () => {
		const view = batteryView(socState(reading(55)), limits(), null, NOW);
		expect(view).toMatchObject({
			pct: 55,
			quality: 'ok',
			kwh: 16.5,
			usableKwh: 30,
			reservePct: 20,
			atOrBelowReserve: false
		});
	});

	it('fehlender oder ungültiger SOC ist missing, nie 0 %', () => {
		for (const state of [stateWith({}), socState(reading(55, 'invalid'))]) {
			expect(batteryView(state, limits(), null, NOW)).toMatchObject({
				pct: null,
				quality: 'missing',
				kwh: null,
				atOrBelowReserve: false
			});
		}
	});

	it('veralteter SOC bleibt sichtbar und ist stale', () => {
		expect(batteryView(socState(reading(55, 'stale')), limits(), null, NOW)).toMatchObject({
			pct: 55,
			quality: 'stale',
			kwh: 16.5
		});
	});

	it('ohne Zustand und Grenzen ist alles unbekannt', () => {
		expect(batteryView(null, null, null, NOW)).toEqual({
			pct: null,
			quality: 'missing',
			kwh: null,
			usableKwh: null,
			reservePct: null,
			powerW: null,
			direction: 'unknown',
			atOrBelowReserve: false,
			gridChargeAllowed: null,
			outlook: null
		});
	});

	it('kennt Richtung und Betrag der Leistung', () => {
		const at = (watts: number | null) =>
			batteryView(socState(reading(55), watts), limits(), null, NOW);
		expect(at(1000)).toMatchObject({ direction: 'charge', powerW: 1000 });
		expect(at(-800)).toMatchObject({ direction: 'discharge', powerW: 800 });
		expect(at(15)).toMatchObject({ direction: 'idle', powerW: 15 });
		expect(at(null)).toMatchObject({ direction: 'unknown', powerW: null });
	});

	it('meldet Reserve bei SOC gleich Reserve', () => {
		expect(batteryView(socState(reading(20)), limits(), null, NOW).atOrBelowReserve).toBe(true);
		expect(batteryView(socState(reading(12)), limits(), null, NOW).atOrBelowReserve).toBe(true);
		expect(batteryView(socState(reading(21)), limits(), null, NOW).atOrBelowReserve).toBe(false);
	});

	it('übernimmt gridChargeAllowed aus dem Plan, ohne Antwort null', () => {
		const state = socState(reading(55));
		expect(
			batteryView(state, limits(), { ...RESPONSE, grid_charge_allowed: true }, NOW)
				.gridChargeAllowed
		).toBe(true);
		expect(
			batteryView(state, limits(), { ...RESPONSE, grid_charge_allowed: false }, NOW)
				.gridChargeAllowed
		).toBe(false);
		expect(batteryView(state, limits(), null, NOW).gridChargeAllowed).toBeNull();
	});

	it('liefert den Ausblick nur aus einem gültigen Plan', () => {
		const state = socState(reading(55));
		const slots = slotsFrom('12:00', [55, 62, 75, 90]);
		expect(batteryView(state, limits(), planResponse({ valid: true, slots }), NOW).outlook).toEqual(
			{
				trend: 'up',
				socPct: 90,
				at: endOf('12:45')
			}
		);
		expect(
			batteryView(state, limits(), planResponse({ valid: false, slots }), NOW).outlook
		).toBeNull();
		expect(batteryView(state, limits(), planResponse(null), NOW).outlook).toBeNull();
	});
});

describe('planOutlook', () => {
	it('steigt bis zum ersten Slot mit dem Maximum des Laufs', () => {
		const slots = slotsFrom('12:00', [55, 62, 75, 90, 90, 86]);
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'up', socPct: 90, at: endOf('12:45') });
	});

	it('sinkt bis zum Tiefpunkt', () => {
		const slots = slotsFrom('12:00', [55, 50, 40, 25, 26]);
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'down', socPct: 25, at: endOf('12:45') });
	});

	it('hält, wenn alles innerhalb der Toleranz bleibt', () => {
		const slots = slotsFrom('12:00', [55, 55.5, 54.5, 55, 55.8]);
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'hold', socPct: 55, at: endOf('12:00') });
	});

	it('zählt vergangene Slots nicht (Ende höchstens jetzt)', () => {
		// 11:45 endet genau um 12:00, 11:30 davor; der Lauf beginnt erst mit dem Slot 12:00
		const slots = slotsFrom('11:30', [10, 20, 55, 50, 40]);
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'down', socPct: 40, at: endOf('12:30') });
	});

	it('ohne künftige Slots gibt es keinen Ausblick', () => {
		expect(planOutlook([], NOW)).toBeNull();
		expect(planOutlook(slotsFrom('10:00', [50, 60, 70]), NOW)).toBeNull();
	});

	it('Schwankungen bis zur Toleranz gegen die Richtung beenden den Lauf nicht', () => {
		const slots = slotsFrom('12:00', [55, 62, 61, 75, 74, 70]);
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'up', socPct: 75, at: endOf('12:45') });
	});

	it('ein größerer Rückgang beendet den Lauf vor dem Rückgang', () => {
		const slots = slotsFrom('12:00', [55, 62, 75, 60, 90]);
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'up', socPct: 75, at: endOf('12:30') });
	});

	it('die Toleranz ist einstellbar', () => {
		const slots = slotsFrom('12:00', [55, 57, 60]);
		expect(planOutlook(slots, NOW, 5)).toEqual({ trend: 'hold', socPct: 55, at: endOf('12:00') });
		expect(planOutlook(slots, NOW)).toEqual({ trend: 'up', socPct: 60, at: endOf('12:30') });
	});
});

describe('outlookText', () => {
	it('beschreibt Anstieg, Abfall und Halten', () => {
		expect(outlookText({ trend: 'up', socPct: 90, at: iso('15:00') }, NOW)).toBe(
			`steigt bis ${formatSoc(90)} um 15:00`
		);
		expect(outlookText({ trend: 'down', socPct: 25, at: iso('23:45') }, NOW)).toBe(
			`sinkt bis ${formatSoc(25)} um 23:45`
		);
		expect(outlookText({ trend: 'hold', socPct: 55, at: iso('12:15') }, NOW)).toBe(
			`hält ${formatSoc(55)}`
		);
	});

	it('nennt „morgen“, wenn das Ziel nach Mitternacht liegt', () => {
		// 10.10., 06:00 in Wien; jetzt ist 09.10., 12:00
		expect(outlookText({ trend: 'down', socPct: 25, at: '2026-10-10T04:00:00+00:00' }, NOW)).toBe(
			`sinkt bis ${formatSoc(25)} morgen um 06:00`
		);
		// Ende des letzten Slots von heute: 00:00 gehört schon zu morgen
		expect(outlookText({ trend: 'up', socPct: 80, at: '2026-10-09T22:00:00+00:00' }, NOW)).toBe(
			`steigt bis ${formatSoc(80)} morgen um 00:00`
		);
	});

	it('nennt danach den Tag', () => {
		expect(outlookText({ trend: 'down', socPct: 25, at: '2026-10-11T04:00:00+00:00' }, NOW)).toBe(
			`sinkt bis ${formatSoc(25)} am So 11.10. um 06:00`
		);
	});
});

describe('Texte der Akku-Kachel', () => {
	const view = (batteryW: number | null, soc = reading(55)) =>
		batteryView(socState(soc, batteryW), limits(), null, NOW);

	it('nennt Richtung und Leistung, ruhend und unbekannt', () => {
		expect(powerText(view(1000))).toBe(`lädt ${formatPower(1000)}`);
		expect(powerText(view(-800))).toBe(`entlädt ${formatPower(800)}`);
		expect(powerText(view(5))).toBe('ruht');
		expect(powerText(view(null))).toBe('–');
	});

	it('rechnet „≈ 16,5 von 30 kWh“, ohne Wert nichts', () => {
		expect(energyText(view(0))).toMatch(/^≈ 16,5 von 30\skWh$/);
		expect(energyText(view(0, reading(55, 'invalid')))).toBeNull();
		expect(energyText(batteryView(socState(reading(55)), null, null, NOW))).toBeNull();
	});

	it('beschriftet das Symbol mit Ladestand und Reserve', () => {
		expect(batteryLabel(view(0))).toBe(`Hausakku ${formatSoc(55)}, Reserve ${formatSoc(20)}`);
		expect(batteryLabel(view(0, reading(55, 'invalid')))).toBe(
			`Hausakku ohne Messwert, Reserve ${formatSoc(20)}`
		);
		expect(batteryLabel(batteryView(socState(reading(55)), null, null, NOW))).toBe(
			`Hausakku ${formatSoc(55)}`
		);
	});

	it('sagt, ob Netzladen erlaubt ist; unbekannt fehlt', () => {
		expect(gridChargeText(true)).toBe('Netzladen erlaubt');
		expect(gridChargeText(false)).toBe('Netzladen aus');
		expect(gridChargeText(null)).toBeNull();
	});
});

describe('reserveEnergyText', () => {
	const base: BatteryView = {
		pct: 25,
		quality: 'ok',
		kwh: 24,
		usableKwh: 96,
		reservePct: 20,
		powerW: null,
		direction: 'unknown',
		atOrBelowReserve: false,
		gridChargeAllowed: null,
		outlook: null
	};

	it('rechnet die Energie über der Reserve', () => {
		expect(reserveEnergyText(base)).toMatch(/^≈ 4,8\skWh über Reserve$/);
	});

	it('rundet einen kleinen Rest auf 0', () => {
		expect(reserveEnergyText({ ...base, pct: 20.04 })).toMatch(/^≈ 0\skWh über Reserve$/);
	});

	it('sagt auf und unter der Reserve „nichts über Reserve“', () => {
		expect(reserveEnergyText({ ...base, pct: 20 })).toBe('nichts über Reserve');
		expect(reserveEnergyText({ ...base, pct: 15 })).toBe('nichts über Reserve');
	});

	it('ohne SOC, Reserve oder Kapazität nichts', () => {
		expect(reserveEnergyText({ ...base, pct: null })).toBeNull();
		expect(reserveEnergyText({ ...base, reservePct: null })).toBeNull();
		expect(reserveEnergyText({ ...base, usableKwh: null })).toBeNull();
	});
});
