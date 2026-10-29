import type { LineSeriesOption } from 'echarts/charts';
import { expect, it, vi } from 'vitest';
import type { Palette } from '$lib/charts/palette';
import { intentEndingAt, planPriceOption, planSocOption, planVehiclesOption } from './chart';
import { slotEndSocPoints } from '$lib/charts/options';
import { formatTime } from '$lib/format';
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
	// zwei Fenster (Halten, Netzladen) als Flächen, ihre Namen stehen in der Legende
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

it('draws the windows with real ECharts, the legend only as chips outside the SVG', async () => {
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
		// die Flächen sind gemalt (Farbe des Netzladens), ihre Namen stehen nicht im Diagramm
		expect(svg).toContain(palette.importPrice);
		expect(svg).not.toContain('Netzladen');
		expect(svg).not.toContain('Halten');
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

it('zeichnet erwartete Autos gestrichelt und benennt sie mit „erwartet ab“', () => {
	const expected = {
		...PLAN.vehicles[0]!,
		key: 'egolf',
		name: 'e-Golf',
		expected: true,
		arrival_at: '2026-10-09T15:30:00+00:00'
	};
	const option = planVehiclesOption(
		{ ...PLAN, vehicles: [...PLAN.vehicles, expected] },
		NOW,
		palette
	) as {
		series: (LineSeriesOption & { lineStyle: { type?: string } })[];
		tooltip: { formatter: (params: unknown) => string };
	};
	expect(option.series.map((s) => s.name)).toEqual(['Tesla Model 3', 'e-Golf erwartet ab 17:30']);
	expect(option.series[0]?.lineStyle.type).toBeUndefined();
	expect(option.series[1]?.lineStyle.type).toBe('dashed');
	// der Tooltip kennt die Reihe unter dem Namen mit „erwartet ab“
	const html = option.tooltip.formatter([
		{ seriesName: 'e-Golf erwartet ab 17:30', value: [Date.UTC(2026, 9, 9, 8, 0), 2.3] }
	]);
	expect(html).toContain('e-Golf erwartet ab 17:30: <b>2,30');
});

it('starts the time axis at the slot of now and keeps the "jetzt" line', () => {
	const first = Date.parse(PLAN.slots[0].start);
	const now = new Date(first + 40 * 60_000).toISOString(); // 40 min nach dem ersten Slot
	for (const option of [planPriceOption(PLAN, now, palette), planSocOption(PLAN, now, palette)] as {
		xAxis: { min: number };
		series: { markLine?: { data: { xAxis: number }[] } }[];
	}[]) {
		expect(option.xAxis.min).toBe(first + 30 * 60_000);
		expect(option.series[0].markLine?.data[0].xAxis).toBe(Date.parse(now));
	}
});

it('starts at the first slot while now is before it', () => {
	const option = planPriceOption(PLAN, NOW, palette) as { xAxis: { min: number } };
	expect(option.xAxis.min).toBe(Date.parse(PLAN.slots[0].start));
});

it('hält in den Plan-Diagrammen den Tooltip im Diagramm und zeichnet die Legende nicht selbst', () => {
	type Chrome = { legend: { show: boolean }; tooltip: { confine: boolean } };
	for (const option of [
		planPriceOption(PLAN, NOW, palette),
		planVehiclesOption(PLAN, NOW, palette)
	] as Chrome[]) {
		expect(option.legend.show).toBe(false);
		expect(option.tooltip.confine).toBe(true);
	}
	expect((planSocOption(PLAN, NOW, palette) as Chrome).tooltip.confine).toBe(true);
});

it('zeichnet „jetzt“ nicht, wenn es außerhalb des Horizonts liegt', () => {
	const later = new Date(Date.parse(PLAN.horizon_end) + 3_600_000).toISOString();
	for (const option of [
		planPriceOption(PLAN, later, palette),
		planSocOption(PLAN, later, palette),
		planVehiclesOption(PLAN, later, palette)
	] as { series: { markLine?: unknown }[] }[]) {
		expect(option.series.every((s) => s.markLine === undefined)).toBe(true);
	}
});

type SocOption = {
	grid: { top: number };
	legend: { show: boolean; data: (string | { name: string; icon: string })[] };
	series: (LineSeriesOption & {
		id: string;
		itemStyle?: { color: string };
		markArea?: { data: { label?: { show: boolean } }[][] };
	})[];
	tooltip: { formatter: (params: unknown) => string };
};

it('beschriftet die Flächen im Akku-Diagramm nicht, die Legende nennt die Absichten', () => {
	const option = planSocOption(planWithWindows(), NOW, palette) as SocOption;
	const pairs = option.series[0].markArea?.data ?? [];
	expect(pairs).toHaveLength(2);
	for (const pair of pairs) expect(pair[0].label?.show).toBe(false);
	// die Chips stehen unter dem Diagramm, oben nur Platz für den Achsentitel; Flächen als Vierecke
	expect(option.legend.show).toBe(false);
	expect(option.grid.top).toBe(28);
	expect(option.legend.data).toEqual([
		'Geplanter SOC',
		'Reserve',
		{ name: 'Netzladen', icon: 'rect' },
		{ name: 'Halten', icon: 'rect' }
	]);
	const netzladen = option.series.find((s) => s.name === 'Netzladen');
	const halten = option.series.find((s) => s.name === 'Halten');
	expect(netzladen?.data).toEqual([]);
	expect(halten?.data).toEqual([]);
	expect(netzladen?.itemStyle?.color).toBe(palette.importPrice);
	expect(halten?.itemStyle?.color).toBe(palette.pv);
});

it('nennt in der Legende nur Absichten, die der Plan hat', () => {
	const slots = planWithWindows().slots.map((s) =>
		s.intent === 'HALTEN' ? { ...s, intent: 'EIGENVERBRAUCH' as const } : s
	);
	const option = planSocOption({ ...PLAN, slots }, NOW, palette) as SocOption;
	expect(option.legend.data).toEqual([
		'Geplanter SOC',
		'Reserve',
		{ name: 'Netzladen', icon: 'rect' }
	]);
	expect(option.series.map((s) => s.name)).not.toContain('Halten');
	const none = planSocOption(PLAN, NOW, palette) as SocOption;
	expect(none.legend.data).toEqual(['Geplanter SOC', 'Reserve']);
});

it('intentEndingAt nennt die Absicht des Slots, der an der Zeit endet, mit seiner Zeitspanne', () => {
	const base = PLAN.slots[0]!;
	const start = Date.parse(base.start);
	const slots = [
		{ ...base, intent: 'NETZLADEN' as const, grid_charge_w: 6700 },
		{ ...base, start: new Date(start + 900_000).toISOString(), intent: 'HALTEN' as const }
	];
	const first = intentEndingAt(slots, start + 900_000);
	expect(first?.label).toBe('Netzladen 6,7 kW');
	expect(first?.from).toBe(new Date(start).toISOString());
	expect(first?.to).toBe(new Date(start + 900_000).toISOString());
	expect(intentEndingAt(slots, start + 1_800_000)?.label).toBe('Halten');
	// der Slot selbst endet nicht am Anfang, und zwischen zwei Enden ist keiner
	expect(intentEndingAt(slots, start)).toBeNull();
	expect(intentEndingAt(slots, start + 300_000)).toBeNull();
	expect(intentEndingAt(slots, start + 2_700_000)).toBeNull();
});

it('zeigt im Tooltip die Absicht des Slots, dessen Ergebnis der SOC-Punkt ist, mit Zeitspanne', () => {
	const plan = planWithWindows();
	const option = planSocOption(plan, NOW, palette) as SocOption;
	const points = slotEndSocPoints(plan.slots);
	const hover = (index: number) =>
		option.tooltip.formatter([
			{ seriesName: 'Geplanter SOC', value: points[index] },
			{ seriesName: 'Reserve', value: [points[index]![0], 20] }
		]);
	const span = (index: number) => {
		const from = formatTime(plan.slots[index]!.start);
		const to = formatTime(new Date(points[index]![0]).toISOString());
		return `${from}\u2013${to}`;
	};
	// der erste Punkt gehört zum Slot 0
	expect(plan.slots[0]!.intent).toBe('EIGENVERBRAUCH');
	expect(hover(0).split('<br>').at(-1)).toBe(`Akku ${span(0)}: <b>Eigenverbrauch</b>`);
	// Halten (Slot 4 und 5) und Netzladen (Slot 6 und 7): der Punkt am Ende von Slot 6
	expect(hover(4).split('<br>').at(-1)).toBe(`Akku ${span(4)}: <b>Halten</b>`);
	const charging = hover(6);
	expect(charging.split('<br>').at(-1)).toBe(`Akku ${span(6)}: <b>Netzladen 2,3 kW</b>`);
	expect(charging.indexOf('Reserve')).toBeLessThan(charging.indexOf('Akku'));
	expect(charging).toContain('Geplanter SOC: <b>');
	// Legendenreihen ohne Daten erscheinen nicht als Zeile
	const withLegend = option.tooltip.formatter([
		{ seriesName: 'Geplanter SOC', value: points[6] },
		{ seriesName: 'Netzladen', value: [points[6]![0], null] }
	]);
	expect(withLegend).not.toContain('Netzladen:');
	// ein Zeitpunkt, an dem kein Slot endet, hat keine Zeile
	const between = option.tooltip.formatter([
		{ seriesName: 'Geplanter SOC', value: [points[6]![0] + 300_000, 55] }
	]);
	expect(between).not.toContain('Akku');
});
