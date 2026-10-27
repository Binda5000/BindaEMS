import { describe, expect, it } from 'vitest';
import {
	CONFIDENCE_LABELS,
	DASH,
	WEEKDAY_LABELS,
	formatCents,
	formatCt,
	formatDayMonthTime,
	formatKelvinShift,
	formatDateTime,
	formatDay,
	formatDayTime,
	formatEnergy,
	formatEur,
	formatNumberMax,
	formatPower,
	formatRatio,
	formatSoc,
	formatTime,
	departureText,
	drivingSourceText,
	expectedLabel,
	suggestionText,
	weekdayDate,
	weekdayGroup,
	weekdayOfDate,
	weakestConfidence
} from './format';

const NB = '\u00a0';

describe('Leistung', () => {
	it('zeigt unter 1 kW Watt, darüber kW', () => {
		expect(formatPower(512)).toBe(`512${NB}W`);
		expect(formatPower(-200.4)).toBe(`-200${NB}W`);
		expect(formatPower(1234)).toBe(`1,23${NB}kW`);
		expect(formatPower(12345)).toBe(`12,3${NB}kW`);
	});

	it('zeigt fehlende Werte als Strich, nie als 0', () => {
		expect(formatPower(null)).toBe(DASH);
		expect(formatPower(undefined)).toBe(DASH);
		expect(formatPower(Number.NaN)).toBe(DASH);
	});
});

it('formatiert Energie, Preise, Geld und Anteile deutsch', () => {
	expect(formatEnergy(12.345)).toBe(`12,3${NB}kWh`);
	expect(formatEnergy(1.234)).toBe(`1,23${NB}kWh`);
	expect(formatCt(13.444)).toBe(`13,44${NB}ct/kWh`);
	expect(formatEur(1.5)).toBe(`1,50${NB}€`);
	expect(formatSoc(55.4)).toBe(`55${NB}%`);
	expect(formatRatio(0.873)).toBe(`87${NB}%`);
	expect(formatEur(null)).toBe(DASH);
	expect(formatEur(-0.002)).toBe(`0,00${NB}€`);
	expect(formatEur(-0.5)).toBe(`-0,50${NB}€`);
});

it('schreibt Euro als Zahl, geschütztes Leerzeichen, € mit österreichischen Tausenderpunkten', () => {
	expect(formatEur(0.52)).toBe(`0,52${NB}€`);
	expect(formatEur(-1.07)).toBe(`-1,07${NB}€`);
	expect(formatEur(1234.5)).toBe(`1.234,50${NB}€`);
	expect(formatEur(-1234567.891)).toBe(`-1.234.567,89${NB}€`);
	expect(formatEur(0)).toBe(`0,00${NB}€`);
	expect(formatEur(-0.004)).toBe(`0,00${NB}€`);
});

it('zeigt Zeiten in Wien', () => {
	expect(formatTime('2026-10-09T08:00:00+00:00')).toBe('10:00');
	expect(formatDateTime('2026-10-09T08:00:00.123456+00:00')).toBe('09.10.2026, 10:00');
	expect(formatDay('2026-10-09')).toBe('Fr., 09.10.');
});

describe('formatNumberMax', () => {
	it('kürzt überflüssige Nachkommastellen', () => {
		expect(formatNumberMax(2, 1)).toBe('2');
		expect(formatNumberMax(2.3, 1)).toBe('2,3');
		expect(formatNumberMax(2.349, 1)).toBe('2,3');
		expect(formatNumberMax(-0.01, 1)).toBe('0');
	});
});

describe('Fahrverhalten', () => {
	it('nennt die Konfidenz auf Deutsch', () => {
		expect(CONFIDENCE_LABELS).toEqual({ high: 'hoch', medium: 'mittel', low: 'niedrig' });
	});

	it('nimmt von zwei Konfidenzen die schwächere, null zählt nicht', () => {
		expect(weakestConfidence('high', 'low')).toBe('low');
		expect(weakestConfidence('medium', 'high')).toBe('medium');
		expect(weakestConfidence('high', null)).toBe('high');
		expect(weakestConfidence(null, null)).toBeNull();
	});

	it('kürzt die Wochentage ab und nennt die Gruppe', () => {
		expect(WEEKDAY_LABELS).toEqual(['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']);
		expect([0, 4, 5, 6].map(weekdayGroup)).toEqual(['Mo–Fr', 'Mo–Fr', 'Sa', 'So/Feiertag']);
		expect(weekdayOfDate('2026-10-12')).toBe('Mo');
		expect(weekdayOfDate('2026-10-18')).toBe('So');
	});

	it('nennt die Quelle der Anwesenheit', () => {
		expect(drivingSourceText('tessie')).toBe('Tessie');
		expect(drivingSourceText('sessions')).toBe('nur Ansteckvorgänge');
		expect(drivingSourceText('home_entity')).toBe('Home Assistant (home_entity)');
		expect(drivingSourceText('ha', 'device_tracker.tesla')).toBe(
			'Home Assistant (device_tracker.tesla)'
		);
		expect(drivingSourceText('ha')).toBe('Home Assistant');
		expect(drivingSourceText('ha', '')).toBe('Home Assistant');
	});

	it('beschriftet ein erwartetes Auto mit der Ankunft in Wiener Zeit', () => {
		expect(expectedLabel('Tesla Model 3', '2026-10-09T15:30:00+00:00')).toBe(
			'Tesla Model 3 erwartet ab 17:30'
		);
	});

	it('schreibt einen Tag als Wochentag und Datum', () => {
		expect(weekdayDate('2026-11-23')).toBe('Mo 23.11.');
		expect(weekdayDate('2026-03-01')).toBe('So 01.03.');
	});

	describe('Abfahrt mit Tag (Ruling F-3)', () => {
		// Freitag, 20.11.2026, 18:00 in Wien
		const now = new Date('2026-11-20T17:00:00Z');
		const monday = '2026-11-23T06:15:00+00:00'; // Mo 07:15
		const saturday = '2026-11-21T06:15:00+00:00'; // Sa 07:15
		const tonight = '2026-11-20T18:30:00+00:00'; // Fr 19:30

		it('nennt den Tag, wenn die Absicht einen Tag nennt, der nicht heute ist', () => {
			expect(departureText(monday, '2026-11-23', now)).toBe('Mo 23.11. 07:15');
			expect(departureText(saturday, '2026-11-21', now)).toBe('Sa 21.11. 07:15');
		});

		it('bleibt bei der Uhrzeit für heute und für eine tägliche Abfahrt', () => {
			expect(departureText(tonight, '2026-11-20', now)).toBe('19:30');
			expect(departureText(saturday, null, now)).toBe('07:15');
		});

		it('nennt ohne Absicht den Tag, wenn er nicht das nächste Auftreten der Uhrzeit ist', () => {
			expect(departureText(monday, undefined, now)).toBe('Mo 23.11. 07:15');
			expect(departureText(saturday, undefined, now)).toBe('07:15');
			expect(departureText(tonight, undefined, now)).toBe('19:30');
		});

		it('nennt den Tag auch für morgen, wenn die Uhrzeit heute noch bevorsteht', () => {
			// So 22.11.2026, 06:00 in Wien: 07:15 kommt heute noch, Montag ist erst das übernächste Mal
			const sundayEarly = new Date('2026-11-22T05:00:00Z');
			expect(departureText(monday, undefined, sundayEarly)).toBe('Mo 23.11. 07:15');
			expect(departureText(monday, '2026-11-23', sundayEarly)).toBe('Mo 23.11. 07:15');
		});

		it('lässt den Tag weg, wenn die Abfahrt das nächste Auftreten der Uhrzeit ist', () => {
			// So 22.11.2026, 08:00 in Wien: 07:15 war schon, der Montag ist das nächste Mal
			const sundayLate = new Date('2026-11-22T07:00:00Z');
			expect(departureText(monday, undefined, sundayLate)).toBe('07:15');
			// am selben Tag: 07:15 kommt heute noch
			const sameDay = '2026-11-22T06:15:00+00:00';
			expect(departureText(sameDay, undefined, new Date('2026-11-22T05:00:00Z'))).toBe('07:15');
		});
	});

	it('schreibt den Vorschlag mit Tag, Abfahrt, Ziel und der schwächeren Konfidenz', () => {
		const trip = {
			date: '2026-10-12',
			departure: '07:15',
			departure_confidence: 'high' as const,
			target_soc_pct: 50,
			target_confidence: 'medium' as const
		};
		expect(suggestionText(trip)).toBe(`Vorschlag: Mo 07:15 · 50${NB}% (Konfidenz mittel)`);
		expect(suggestionText({ ...trip, target_soc_pct: null, target_confidence: null })).toBe(
			'Vorschlag: Mo 07:15 (Konfidenz hoch)'
		);
	});
});

describe('formatDayTime', () => {
	// Mi 21.10.2026, 14:00 in Wien
	const now = new Date('2026-10-21T12:00:00Z');

	it('nennt heute nur die Uhrzeit', () => {
		expect(formatDayTime(new Date('2026-10-21T15:30:00Z'), now)).toBe('17:30');
		expect(formatDayTime(new Date('2026-10-21T08:00:00Z'), now)).toBe('10:00'); // schon vorbei
	});

	it('nennt morgen „morgen“', () => {
		expect(formatDayTime(new Date('2026-10-22T05:15:00Z'), now)).toBe('morgen 07:15');
	});

	it('nennt später Wochentag und Datum', () => {
		expect(formatDayTime(new Date('2026-10-23T05:15:00Z'), now)).toBe('Fr 23.10. 07:15');
	});

	it('rechnet die Tage in Wien: kurz vor Mitternacht UTC ist schon der nächste Tag', () => {
		// 21.10. 22:30 UTC = 22.10. 00:30 in Wien: morgen
		expect(formatDayTime(new Date('2026-10-21T22:30:00Z'), now)).toBe('morgen 00:30');
	});

	it('kennt die Zeitumstellung (Sommerzeit endet am 25.10.2026)', () => {
		const eve = new Date('2026-10-24T12:00:00Z'); // Sa 14:00
		expect(formatDayTime(new Date('2026-10-25T12:00:00Z'), eve)).toBe('morgen 13:00');
	});
});

describe('Vorheizen und Vorkühlen', () => {
	it('zeigt den Versatz mit Vorzeichen und echtem Minus', () => {
		expect(formatKelvinShift(1)).toBe(`+1,0${NB}K`);
		expect(formatKelvinShift(-0.5)).toBe(`−0,5${NB}K`);
		expect(formatKelvinShift(0)).toBe(`0,0${NB}K`);
		expect(formatKelvinShift(-0.01)).toBe(`0,0${NB}K`);
		expect(formatKelvinShift(null)).toBe(DASH);
	});

	it('zeigt Cent mit höchstens einer Nachkommastelle', () => {
		expect(formatCents(12)).toBe(`12${NB}ct`);
		expect(formatCents(18.44)).toBe(`18,4${NB}ct`);
		expect(formatCents(null)).toBe(DASH);
	});

	it('zeigt Tag und Uhrzeit ohne Jahr in Wien', () => {
		expect(formatDayMonthTime('2026-10-09T01:50:00+00:00')).toBe('09.10. 03:50');
		expect(formatDayMonthTime('2026-12-31T23:05:00Z')).toBe('01.01. 00:05');
		expect(formatDayMonthTime('kaputt')).toBe(DASH);
	});
});
