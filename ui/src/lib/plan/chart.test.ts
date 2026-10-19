import type { LineSeriesOption } from 'echarts/charts';
import { expect, it, vi } from 'vitest';
import type { Palette } from '$lib/charts/palette';
import { planPriceOption, planSocOption, planVehiclesOption } from './chart';
import { NOW, PLAN, planWithWindows } from '$lib/testing/plan';

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

type Data = [number, number | null][];

it('marks estimated prices and intent windows', () => {
	const plan = planWithWindows();
	const price = planPriceOption(plan, NOW, palette) as { series: { name: string }[] };
	expect(price.series.map((s) => s.name)).toEqual([
		'Bezugspreis',
		'Bezugspreis geschätzt',
		'PV-Prognose',
		'Lastprognose'
	]);
	const soc = planSocOption(plan, NOW, palette) as {
		series: { name: string; markArea?: { data: unknown[] } }[];
	};
	expect(soc.series[0].name).toBe('Geplanter SOC');
	expect(soc.series[1].name).toBe('Reserve');
	const areas = JSON.stringify(soc.series[0].markArea);
	expect(areas).toContain('Halten');
	expect(areas).toContain('Netzladen');
	expect(soc.series[0].markArea?.data).toHaveLength(2);
});

it('draws estimated prices dashed and only over estimated slots', () => {
	const plan = planWithWindows();
	const series = (planPriceOption(plan, NOW, palette) as { series: LineSeriesOption[] }).series;
	const real = series[0].data as Data;
	const estimated = series[1].data as Data;
	expect((series[1].lineStyle as { type?: string }).type).toBe('dashed');
	const firstEstimated = plan.slots.findIndex((s) => s.estimated);
	expect(firstEstimated).toBeGreaterThan(0);
	expect(real[0][1]).not.toBeNull();
	expect(real[firstEstimated][1]).toBeNull();
	expect(estimated[0][1]).toBeNull();
	expect(estimated[firstEstimated][1]).toBe(plan.slots[firstEstimated].import_ct);
	// ein Schlusspunkt am Ende des letzten Slots
	expect(real).toHaveLength(plan.slots.length + 1);
});

it('shows PV and load in kW', () => {
	const series = (planPriceOption(PLAN, NOW, palette) as { series: LineSeriesOption[] }).series;
	const pv = series[2].data as Data;
	expect(pv[0][1]).toBeCloseTo(PLAN.slots[0].pv_w / 1000);
	const load = series[3].data as Data;
	expect(load[0][1]).toBeCloseTo(PLAN.slots[0].load_w / 1000);
});

it('plots the planned SOC at the end of each slot with the reserve line', () => {
	const series = (planSocOption(PLAN, NOW, palette) as { series: LineSeriesOption[] }).series;
	const soc = series[0].data as Data;
	expect(soc[0]).toEqual([Date.parse(PLAN.slots[0].start) + 900_000, PLAN.slots[0].soc_pct]);
	const reserve = series[1].data as Data;
	expect(reserve.every(([, value]) => value === PLAN.reserve_pct)).toBe(true);
});

it('shows no windows when the plan only covers own consumption', () => {
	const soc = planSocOption(PLAN, NOW, palette) as {
		series: { markArea?: { data: unknown[] } }[];
	};
	expect(soc.series[0].markArea?.data ?? []).toEqual([]);
});

it('draws the windows with real ECharts (labels in the SVG)', async () => {
	// jsdom kann kein Canvas; ECharts misst Text dann näherungsweise
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
	const core = await import('echarts/core');
	const { LineChart } = await import('echarts/charts');
	const components = await import('echarts/components');
	const { SVGRenderer } = await import('echarts/renderers');
	core.use([
		LineChart,
		components.GridComponent,
		components.LegendComponent,
		components.MarkLineComponent,
		components.MarkAreaComponent,
		SVGRenderer
	]);
	const chart = core.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 300 });
	try {
		chart.setOption(planSocOption(planWithWindows(), NOW, palette));
		const svg = chart.renderToSVGString();
		expect(svg).toContain('Netzladen');
		expect(svg).toContain('Halten');
	} finally {
		chart.dispose();
		vi.restoreAllMocks();
	}
});

it('zeichnet je geplantem Auto eine Reihe mit seinem Namen in kW', () => {
	const option = planVehiclesOption(PLAN, NOW, palette) as {
		series: LineSeriesOption[];
		yAxis: { name: string }[];
	};
	expect(PLAN.vehicles.map((car) => car.name)).toEqual(['Tesla Model 3']);
	expect(option.series.map((s) => s.name)).toEqual(['Tesla Model 3']);
	expect(option.yAxis[0].name).toBe('kW');
	const data = option.series[0].data as Data;
	expect(data).toHaveLength(PLAN.slots.length + 1); // Stufen mit Schlusspunkt
	const charging = PLAN.slots.findIndex((slot) => (slot.ev_w.tesla ?? 0) > 0);
	expect(charging).toBeGreaterThanOrEqual(0);
	expect(data[charging][1]).toBeCloseTo(PLAN.slots[charging].ev_w.tesla! / 1000);
	// ohne Ladeleistung im Slot steht 0 kW, nicht „keine Angabe“
	const idle = PLAN.slots.findIndex((slot) => slot.ev_w.tesla === undefined);
	expect(data[idle][1]).toBe(0);
});

it('kennt ohne geplante Autos keine Reihe', () => {
	const option = planVehiclesOption({ ...PLAN, vehicles: [] }, NOW, palette) as {
		series: LineSeriesOption[];
	};
	expect(option.series).toEqual([]);
});
