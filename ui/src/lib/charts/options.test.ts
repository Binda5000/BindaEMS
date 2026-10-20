import type { BarSeriesOption, LineSeriesOption, YAXisComponentOption } from 'echarts';
import { describe, expect, it } from 'vitest';
import type { LoadForecast, PriceSlot } from '$lib/api/schemas';
import { stats } from '$lib/testing/fixtures';
import { axisTooltip, historyOption, priceForecastOption, statsEnergyOption } from './options';
import type { Palette } from './palette';

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	load: '#80f',
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
			null,
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
			[{ start: '2026-10-09T08:00:00+00:00', p50_w: 2500, p25_w: 1750, p75_w: 3000 }],
			null,
			'2026-10-09T08:05:00+00:00',
			palette
		);
		const series = option.series as LineSeriesOption[];
		expect(series.map((s) => s.name)).toEqual([
			'Bezugspreis',
			'PV P25',
			'PV P25–P75',
			'PV-Prognose'
		]);
		const pv = series.find((s) => s.name === 'PV-Prognose');
		expect([pv?.yAxisIndex, pv?.data]).toEqual([1, [[at(0), 2.5]]]);
		expect((option.yAxis as YAXisComponentOption[]).map((axis) => axis.name)).toEqual([
			'ct/kWh',
			'kW'
		]);
		expect(JSON.stringify(series[0].markLine)).toContain(String(at(5)));
	});

	const FORECAST = [
		{ start: '2026-10-09T08:00:00+00:00', p50_w: 2500, p25_w: 1750, p75_w: 3000 },
		{ start: '2026-10-09T08:15:00+00:00', p50_w: 2600, p25_w: 1800, p75_w: 3200 }
	];
	const LOAD: LoadForecast = {
		issued_at: '2026-10-09T08:00:00+00:00',
		slots: [
			{ start: '2026-10-09T08:00:00+00:00', p50_w: 900 },
			{ start: '2026-10-09T08:15:00+00:00', p50_w: 1100 }
		],
		days: [],
		method: 'profile',
		basis: { ledger: 10, ha: 5 },
		status: { name: 'load-forecast', ok: true, message: 'ok', since: null, details: {} }
	};
	const PRICES = [slot('2026-10-09T08:00:00+00:00', 13.44)];
	const NOW = '2026-10-09T08:05:00+00:00';

	it('zeichnet das PV-Band und die Lastprognose', () => {
		const option = priceForecastOption(PRICES, FORECAST, LOAD, NOW, palette);
		const series = option.series as LineSeriesOption[];
		expect(series.map((s) => s.name)).toEqual(
			expect.arrayContaining(['PV-Prognose', 'PV P25–P75', 'Lastprognose'])
		);
		const base = series.find((s) => s.name === 'PV P25');
		const band = series.find((s) => s.name === 'PV P25–P75');
		const load = series.find((s) => s.name === 'Lastprognose');
		// gestapelt: unsichtbarer Sockel P25, darüber P75 − P25 (dritter Wert: P75 für den Tooltip)
		expect(base?.stack).toBe(band?.stack);
		expect(base?.data).toEqual([
			[at(0), 1.75],
			[at(15), 1.8]
		]);
		expect(band?.data).toEqual([
			[at(0), 1.25, 3],
			[at(15), 1.4, 3.2]
		]);
		expect(band?.lineStyle).toMatchObject({ width: 0 });
		expect(band?.showSymbol).toBe(false);
		expect(load?.data).toEqual([
			[at(0), 0.9],
			[at(15), 1.1]
		]);
		expect(load?.lineStyle).toMatchObject({ type: 'dashed' });
		expect(load?.yAxisIndex).toBe(1);
	});

	it('funktioniert ohne Lastprognose', () => {
		const option = priceForecastOption(PRICES, FORECAST, null, NOW, palette);
		const series = option.series as LineSeriesOption[];
		expect(series.some((s) => s.name === 'Lastprognose')).toBe(false);
	});

	it('zeigt die Lastprognose auch ohne PV-Prognose auf der kW-Achse', () => {
		const option = priceForecastOption(PRICES, [], LOAD, NOW, palette);
		const axes = option.yAxis as { show: boolean }[];
		expect(axes[1].show).toBe(true);
		expect((option.series as LineSeriesOption[]).some((s) => s.name === 'PV P25–P75')).toBe(false);
	});

	it('führt im Tooltip P25 und P75 des Bandes, nicht die Differenz, und blendet den Sockel aus', () => {
		const option = priceForecastOption(PRICES, FORECAST, LOAD, NOW, palette);
		const formatter = (option.tooltip as { formatter: (params: unknown) => string }).formatter;
		const text = formatter([
			{ seriesName: 'PV P25', value: [at(0), 1.75] },
			{ seriesName: 'PV P25–P75', value: [at(0), 1.25, 3] },
			{ seriesName: 'PV-Prognose', value: [at(0), 2.5] }
		]);
		expect(text).toContain('1,75');
		expect(text).toContain('3,00');
		expect(text).not.toContain('1,25');
		expect(text).not.toContain('PV P25:');
		expect(text).toContain('PV-Prognose');
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

describe('Energie der Statistik', () => {
	const bucket = (start: string, pv: number, imp: number, exp: number, use: number) => ({
		start,
		pv_kwh: pv,
		import_kwh: imp,
		export_kwh: exp,
		consumption_kwh: use,
		net_eur: 0
	});
	const month = stats({
		period: 'month',
		buckets: [
			bucket('2026-10-01', 29.037, 0, 4.714, 24.133),
			bucket('2026-10-02', 18.339, 1.5, 0, 24.133)
		]
	});

	it('zeichnet PV, Bezug, Einspeisung und Verbrauch je Tag', () => {
		const option = statsEnergyOption(month, palette);
		const series = option.series as (BarSeriesOption | LineSeriesOption)[];
		expect(series.map((s) => [s.name, s.type])).toEqual([
			['PV', 'bar'],
			['Bezug', 'bar'],
			['Einspeisung', 'bar'],
			['Verbrauch', 'line']
		]);
		expect(series.map((s) => s.data)).toEqual([
			[29.037, 18.339],
			[0, 1.5],
			[4.714, 0],
			[24.133, 24.133]
		]);
		expect((option.xAxis as { data: string[] }).data).toEqual(['01.10.', '02.10.']);
		expect((option.yAxis as YAXisComponentOption).name).toBe('kWh');
	});

	it('nimmt die Farben aus der Palette', () => {
		const series = statsEnergyOption(month, palette).series as {
			itemStyle: { color: string };
		}[];
		expect(series.map((s) => s.itemStyle.color)).toEqual([
			palette.pv,
			palette.series[0],
			palette.feedIn,
			palette.load
		]);
	});

	it('hat an Tagen mit Zeitumstellung 23 bzw. 25 Stunden mit verschiedenen Beschriftungen', () => {
		// Frühjahr: 28.03. 23:00 UTC bis 29.03. (23 h); Herbst: 24.10. 22:00 UTC bis 25.10. (25 h)
		const hours = (from: number, count: number) =>
			Array.from({ length: count }, (_, i) => {
				const at = new Date(from + i * 3_600_000).toISOString();
				return bucket(at, 0, 0, 0, 0);
			});
		const cases: [number, number][] = [
			[Date.UTC(2026, 2, 28, 23), 23],
			[Date.UTC(2026, 9, 24, 22), 25]
		];
		for (const [from, count] of cases) {
			const option = statsEnergyOption(
				stats({ period: 'day', buckets: hours(from, count) }),
				palette
			);
			const labels = (option.xAxis as { data: string[] }).data;
			expect(labels).toHaveLength(count);
			expect(new Set(labels).size).toBe(count);
			expect((option.series as { data: number[] }[])[0].data).toHaveLength(count);
		}
		const fall = statsEnergyOption(
			stats({ period: 'day', buckets: hours(cases[1][0], 25) }),
			palette
		);
		const labels = (fall.xAxis as { data: string[] }).data;
		expect(labels.slice(1, 5)).toEqual(['01:00', '02:00 MESZ', '02:00 MEZ', '03:00']);
	});

	it('beschriftet das Jahr mit Monaten', () => {
		const option = statsEnergyOption(
			stats({ period: 'year', buckets: [bucket('2026-01', 0, 0, 0, 0)] }),
			palette
		);
		expect((option.xAxis as { data: string[] }).data).toEqual(['Jän.']);
	});

	it('zeigt im Tooltip Beschriftung und kWh je Reihe', () => {
		const option = statsEnergyOption(month, palette);
		const formatter = (option.tooltip as { formatter: (params: unknown) => string }).formatter;
		const text = formatter([
			{ name: '01.10.', seriesName: 'PV', value: 29.037, marker: '' },
			{ name: '01.10.', seriesName: 'Bezug', value: 0, marker: '' }
		]);
		expect(text).toContain('01.10.');
		expect(text).toContain('PV: <b>29,0');
		expect(text).toContain('Bezug: <b>0,00');
		expect(text).toContain('kWh');
	});
});
