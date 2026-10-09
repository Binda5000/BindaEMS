import type { LineSeriesOption, YAXisComponentOption } from 'echarts';
import { describe, expect, it } from 'vitest';
import type { PriceSlot } from '$lib/api/schemas';
import { priceForecastOption } from './options';
import type { Palette } from './palette';

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	series: ['#00f', '#0a0']
};

const slot = (start: string, ct: number, feedIn: number | null = 7.3): PriceSlot => ({
	start,
	spot_net_ct: 9,
	import_net_ct: ct / 1.2,
	import_gross_ct: ct,
	feed_in_ct: feedIn,
	origin: 'primary',
	missing: []
});

const at = (minute: number) => Date.UTC(2026, 9, 9, 8, minute);

describe('Preise und Prognose', () => {
	it('zeichnet Preise als Stufen und schließt den letzten Slot ab', () => {
		const option = priceForecastOption(
			[slot('2026-10-09T08:00:00+00:00', 13.44), slot('2026-10-09T08:15:00+00:00', 15)],
			[],
			'2026-10-09T08:05:00+00:00',
			palette
		);
		const series = option.series as LineSeriesOption[];
		expect(series.map((s) => s.name)).toEqual(['Bezugspreis', 'Einspeisung']);
		expect([series[0].step, series[0].yAxisIndex]).toEqual(['end', 0]);
		expect(series[0].data).toEqual([
			[at(0), 13.44],
			[at(15), 15],
			[at(30), 15]
		]);
	});

	it('zeigt die PV-Prognose in kW auf der zweiten Achse und markiert jetzt', () => {
		const option = priceForecastOption(
			[slot('2026-10-09T08:00:00+00:00', 13.44, null)],
			[{ start: '2026-10-09T08:00:00+00:00', p50_w: 2500 }],
			'2026-10-09T08:05:00+00:00',
			palette
		);
		const series = option.series as LineSeriesOption[];
		expect(series.map((s) => s.name)).toEqual(['Bezugspreis', 'PV-Prognose']);
		expect([series[1].yAxisIndex, series[1].data]).toEqual([1, [[at(0), 2.5]]]);
		expect((option.yAxis as YAXisComponentOption[]).map((axis) => axis.name)).toEqual([
			'ct/kWh',
			'kW'
		]);
		expect(JSON.stringify(series[0].markLine)).toContain(String(at(5)));
	});
});
