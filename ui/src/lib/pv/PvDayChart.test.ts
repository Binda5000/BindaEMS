import { render, screen, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { PvDay } from '$lib/api/schemas';
import { readPalette } from '$lib/charts/palette';
import { HUAWEI, SUED, WEST } from '$lib/testing/pv';
import { dayOption } from './charts';
import PvDayChart from './PvDayChart.svelte';

vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

// 08.11.2026 in Wien: 23:00 UTC am Vortag bis 23:00 UTC
const START = Date.UTC(2026, 10, 7, 23);
const at = (minutes: number) => START + minutes * 60_000;

const DAY: PvDay = {
	date: '2026-11-08',
	series: [
		{
			id: 1,
			points: [
				[at(600), 0],
				[at(605), 2960],
				[at(625), 3000]
			],
			note: null
		},
		{ id: 2, points: [[at(600), 1840]], note: null },
		{ id: 3, points: [], note: 'HA-Datenbank nicht eingerichtet' }
	]
};

interface Line {
	name?: string;
	data: [number, number | null][];
	lineStyle?: { color: string; type: string };
	markLine?: { data: { xAxis: number }[] };
}

function option(now: number | null = null) {
	return dayOption(DAY, [HUAWEI, SUED, WEST], readPalette(), now) as {
		xAxis: { min: number; max: number };
		series: Line[];
		tooltip: { formatter: (params: unknown) => string };
	};
}

describe('dayOption', () => {
	it('zeichnet eine Linie je Eintrag in seiner Farbe, die Anlage mit Strings gestrichelt', () => {
		const lines = option().series.filter((line) => line.name);
		expect(lines.map((line) => [line.name, line.lineStyle?.color, line.lineStyle?.type])).toEqual([
			['Huawei', '#6e7781', 'dashed'],
			['Süd', '#d18f00', 'solid'],
			['West', '#1f6feb', 'solid']
		]);
	});

	it('rechnet in kW und lässt fehlende 5-Minuten-Werte als Lücke', () => {
		const [huawei] = option().series;
		expect(huawei?.data).toEqual([
			[at(600), 0],
			[at(605), 2.96],
			[at(610), null],
			[at(625), 3]
		]);
	});

	it('reicht über den ganzen Tag und zeigt „jetzt“ nur an diesem Tag', () => {
		const { xAxis, series } = option(at(615));
		expect([xAxis.min, xAxis.max]).toEqual([START, START + 24 * 3_600_000]);
		expect(series.at(-1)?.markLine?.data).toEqual([{ xAxis: at(615) }]);
		expect(option(START - 1).series.at(-1)?.markLine).toBeUndefined();
	});

	it('nennt im Tooltip Zeit, Name und Leistung', () => {
		const text = option().tooltip.formatter([
			{ seriesName: 'Süd', marker: '', value: [at(690), 2.1] },
			{ seriesName: 'West', marker: '', value: [at(690), 0.95] },
			{ seriesName: undefined, marker: '', value: [at(690), null] }
		]);
		expect(text).toBe('11:30<br>Süd <b>2,10 kW</b><br>West <b>950 W</b>');
	});
});

describe('PvDayChart', () => {
	it('zeigt die Kurve mit Chips je Eintrag und nennt leere Kurven mit Grund', () => {
		render(PvDayChart, { day: DAY, sources: [HUAWEI, SUED, WEST], now: null });
		expect(screen.getByRole('heading', { name: 'Leistung' })).toBeInTheDocument();
		expect(screen.getByRole('img', { name: /^Leistung am/ })).toBeInTheDocument();
		const group = screen.getByRole('group', { name: /^Reihen von/ });
		expect(
			within(group)
				.getAllByRole('button')
				.map((b) => b.textContent?.trim())
		).toEqual(['Huawei', 'Süd', 'West']);
		expect(screen.getByText('West: HA-Datenbank nicht eingerichtet')).toBeInTheDocument();
	});
});
