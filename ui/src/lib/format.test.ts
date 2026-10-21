import { describe, expect, it } from 'vitest';
import {
	CONFIDENCE_LABELS,
	DASH,
	WEEKDAY_LABELS,
	formatCt,
	formatDateTime,
	formatDay,
	formatEnergy,
	formatEur,
	formatNumberMax,
	formatPower,
	formatRatio,
	formatSoc,
	formatTime,
	drivingSourceText,
	expectedLabel,
	suggestionText,
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
