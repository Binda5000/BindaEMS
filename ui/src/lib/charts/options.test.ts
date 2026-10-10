import type { LineSeriesOption, YAXisComponentOption } from 'echarts';
import { describe, expect, it } from 'vitest';
import type { PriceSlot } from '$lib/api/schemas';
import { axisTooltip, historyOption, priceForecastOption } from './options';
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

describe('Verlauf', () => {
	const data = {
		rp: 'raw' as const,
		step_s: 60,
		series: {
			grid: {
				label: 'Netz',
				unit: 'W',
				points: [
					[0, 1000],
					[60_000, 2000],
					[300_000, 3000]
				] as [number, number][]
			},
			'soc.battery': { label: 'Akku-SOC', unit: '%', points: [[0, 55]] as [number, number][] }
		}
	};

	it('trägt jede Einheit auf eigener Achse, Leistung in kW', () => {
		const option = historyOption(data, palette);
		expect((option.yAxis as YAXisComponentOption[]).map((axis) => axis.name)).toEqual(['kW', '%']);
		const [grid, soc] = option.series as LineSeriesOption[];
		expect([grid.name, grid.yAxisIndex, soc.name, soc.yAxisIndex]).toEqual([
			'Netz',
			0,
			'Akku-SOC',
			1
		]);
	});

	it('unterbricht die Linie bei Lücken', () => {
		const [grid] = historyOption(data, palette).series as LineSeriesOption[];
		expect(grid.data).toEqual([
			[0, 1],
			[60_000, 2],
			[120_000, null],
			[300_000, 3]
		]);
	});
	it('bricht Reihen im Viertelstundenraster bei kleinerem Schritt nicht auf', () => {
		// Preis und Prognose liegen je Viertelstunde vor, ein Tag wird aber mit 5 min abgefragt
		const quarter = [0, 1, 2, 4].map((k): [number, number] => [k * 900_000, 20 + k]);
		const prices = {
			rp: 'long' as const,
			step_s: 300,
			series: { price: { label: 'Bezugspreis', unit: 'ct/kWh', points: quarter } }
		};
		const [price] = historyOption(prices, palette).series as LineSeriesOption[];
		expect(price.data).toEqual([
			[0, 20],
			[900_000, 21],
			[1_800_000, 22],
			[2_700_000, null],
			[3_600_000, 24]
		]);
	});
});

it('setzt Reihennamen im Tooltip als Text, nicht als HTML', () => {
	const tooltip = axisTooltip({});
	const html = tooltip([{ seriesName: 'SOC <b>Golf</b> & Co', value: [0, 42] }]);
	expect(html).toContain('SOC &lt;b&gt;Golf&lt;/b&gt; &amp; Co');
	expect(html).not.toContain('<b>Golf</b>');
});
