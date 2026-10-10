import { expect, it } from 'vitest';
import { daySummary } from '$lib/testing/fixtures';
import { balanceRows } from './balance';

it('formatiert eine Tageszeile', () => {
	const [row] = balanceRows([
		daySummary({
			date: '2026-10-09',
			slots: 4,
			expected_slots: 96,
			coverage: 0.0417,
			pv_kwh: 3.2,
			wallbox_kwh: { evcs: 0, twc: 1.38 },
			revenue_eur: null,
			autarky: 0.873
		})
	]);
	expect(row.day).toBe('Fr., 09.10.');
	expect([row.cells.coverage, row.cells.pv, row.cells.wallboxes]).toEqual([
		'4 von 96 · 4,1\u00a0%',
		'3,20 kWh',
		'1,38 kWh'
	]);
	expect([row.cells.revenue, row.cells.autarky]).toEqual(['–', '87 %']);
});

it('Tagesbilanz zeigt 100 Viertelstunden am 25.10.', () => {
	const [row] = balanceRows([
		daySummary({ date: '2026-10-25', slots: 100, expected_slots: 100, coverage: 1 })
	]);
	expect(row.cells.coverage).toBe('100 von 100 · 100\u00a0%');
});

it('nennt 100 % nur ohne jede Lücke (alle Slots da, aber 10 s gefehlt)', () => {
	const [gap, full] = balanceRows([
		daySummary({ slots: 96, expected_slots: 96, coverage: 1 - 10 / 86_400 }),
		daySummary({ slots: 96, expected_slots: 96, coverage: 1 })
	]);
	expect([gap.cells.coverage, full.cells.coverage]).toEqual([
		'96 von 96 · 99,9\u00a0%',
		'96 von 96 · 100\u00a0%'
	]);
});

it('listet die Zählerstände für den Abgleich mit VRM', () => {
	const [row] = balanceRows([
		daySummary({ counter_kwh: { 'grid.energy_import_kwh': 12.345, 'pv.huawei.energy_kwh': 3.21 } })
	]);
	expect(row.counters).toEqual([
		{ name: 'Netz Bezug', value: '12,3 kWh' },
		{ name: 'PV huawei', value: '3,21 kWh' }
	]);
});
