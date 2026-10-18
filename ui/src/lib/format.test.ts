import { describe, expect, it } from 'vitest';
import {
	DASH,
	formatCt,
	formatDateTime,
	formatDay,
	formatEnergy,
	formatEur,
	formatNumberMax,
	formatPower,
	formatRatio,
	formatSoc,
	formatTime
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
