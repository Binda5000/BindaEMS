import { describe, expect, it } from 'vitest';
import type { HistoryResponse } from '$lib/api/schemas';
import { stats } from '$lib/testing/fixtures';
import {
	dayEnergy,
	gridLabel,
	HISTORY_SERIES,
	SPARK_WINDOW_MS,
	sparkLine,
	sparkPoints
} from './tiles';

const NOW = Date.UTC(2026, 9, 9, 12, 0);
const MINUTE = 60_000;

function history(points: [number, number][], id = 'pv'): HistoryResponse {
	return { rp: 'raw', step_s: 60, series: { [id]: { label: 'PV', unit: 'W', points } } };
}

describe('sparkPoints', () => {
	it('behält nur die letzten 2 h, sortiert nach Zeit', () => {
		const inside: [number, number][] = [
			[NOW - 30 * MINUTE, 3],
			[NOW - 119 * MINUTE, 1],
			[NOW - 60 * MINUTE, 2]
		];
		const outside: [number, number][] = [[NOW - 121 * MINUTE, 9]];
		expect(sparkPoints(history([...outside, ...inside]), 'pv', NOW)).toEqual([
			[NOW - 119 * MINUTE, 1],
			[NOW - 60 * MINUTE, 2],
			[NOW - 30 * MINUTE, 3]
		]);
	});

	it('die Grenze bei genau 2 h gehört noch dazu', () => {
		expect(sparkPoints(history([[NOW - SPARK_WINDOW_MS, 5]]), 'pv', NOW)).toEqual([
			[NOW - SPARK_WINDOW_MS, 5]
		]);
	});

	it('fehlende Reihe oder fehlender Verlauf ergibt eine leere Liste', () => {
		expect(sparkPoints(history([[NOW, 1]]), 'house', NOW)).toEqual([]);
		expect(sparkPoints(null, 'pv', NOW)).toEqual([]);
		expect(sparkPoints(undefined, 'pv', NOW)).toEqual([]);
	});

	it('ändert die Eingabe nicht', () => {
		const data = history([
			[NOW - 10 * MINUTE, 2],
			[NOW - 20 * MINUTE, 1]
		]);
		sparkPoints(data, 'pv', NOW);
		expect(data.series.pv?.points[0]).toEqual([NOW - 10 * MINUTE, 2]);
	});

	it('fragt die vier Reihen der Kacheln ab', () => {
		expect(HISTORY_SERIES).toEqual(['pv', 'house', 'grid', 'soc.battery']);
	});
});

describe('gridLabel', () => {
	it('benennt Bezug und Einspeisung', () => {
		expect(gridLabel(380)).toBe('Netz · Bezug');
		expect(gridLabel(-900)).toBe('Netz · Einspeisung');
	});

	it('nennt bei Ruhe oder ohne Wert nur „Netz“', () => {
		expect(gridLabel(10)).toBe('Netz');
		expect(gridLabel(-10)).toBe('Netz');
		expect(gridLabel(null)).toBe('Netz');
		expect(gridLabel(undefined)).toBe('Netz');
	});
});

describe('dayEnergy', () => {
	it('ohne Statistik fehlen alle Werte', () => {
		const empty = { pvKwh: null, houseKwh: null, importKwh: null, exportKwh: null };
		expect(dayEnergy(null)).toEqual(empty);
		expect(dayEnergy(undefined)).toEqual(empty);
	});

	it('übernimmt die vier Felder aus der Statistik', () => {
		const data = stats();
		expect(dayEnergy(data)).toEqual({
			pvKwh: data.energy.pv_kwh,
			houseKwh: data.energy.house_kwh,
			importKwh: data.energy.import_kwh,
			exportKwh: data.energy.export_kwh
		});
	});
});

describe('sparkLine', () => {
	it('skaliert Zeit und Wert auf 100 × 24, größter Wert oben', () => {
		expect(
			sparkLine([
				[0, 0],
				[10, 10],
				[20, 5]
			])
		).toBe('0,22 50,2 100,12');
	});

	it('zeichnet eine gleichbleibende Reihe in der Mitte', () => {
		expect(
			sparkLine([
				[0, 7],
				[10, 7]
			])
		).toBe('0,12 100,12');
	});

	it('braucht mindestens zwei Punkte', () => {
		expect(sparkLine([])).toBeNull();
		expect(sparkLine([[0, 1]])).toBeNull();
	});
});
