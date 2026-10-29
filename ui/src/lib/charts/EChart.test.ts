import { fireEvent, render, screen } from '@testing-library/svelte';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';
import * as v from 'valibot';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import historyJson from '$lib/api/contract/history.json';
import { HistoryResponseSchema, type HistoryResponse } from '$lib/api/schemas';
import EChart from './EChart.svelte';
import { historyOption } from './options';
import { readPalette } from './palette';
import { formatTime } from '$lib/format';

// echtes ECharts ohne Canvas (jsdom): serverseitig als SVG mit fester Größe gezeichnet
const { charts } = vi.hoisted(() => ({ charts: [] as EChartsType[] }));
vi.mock('./echarts', async () => {
	const core = await import('echarts/core');
	const { LineChart } = await import('echarts/charts');
	const components = await import('echarts/components');
	const { SVGRenderer } = await import('echarts/renderers');
	core.use([
		LineChart,
		components.GridComponent,
		components.TooltipComponent,
		components.LegendComponent,
		components.DataZoomComponent,
		components.MarkLineComponent,
		SVGRenderer
	]);
	const init = () => {
		const chart = core.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 300 });
		charts.push(chart);
		return chart;
	};
	return { loadECharts: () => Promise.resolve({ ...core, init }) };
});

interface ShownOption {
	animation?: unknown;
	xAxis?: { axisLabel?: { customValues?: number[] } }[];
	series?: unknown[];
	dataZoom?: { start: number; end: number }[];
	legend?: { selected?: Record<string, boolean> }[];
}

const history = v.parse(HistoryResponseSchema, historyJson);
const palette = readPalette();

function scaled(data: HistoryResponse, factor: number): HistoryResponse {
	const series = Object.fromEntries(
		Object.entries(data.series).map(([key, entry]) => [
			key,
			{
				...entry,
				points: entry.points.map(([time, value]): [number, number] => [time, value * factor])
			}
		])
	);
	return { ...data, series };
}

function shown(chart: EChartsType): ShownOption {
	return chart.getOption() as ShownOption;
}

async function drawn(): Promise<EChartsType> {
	await vi.waitFor(() => expect(shown(charts[0]).series?.length).toBeGreaterThan(0));
	return charts[0];
}

/** Option mit den Reihen PV und Haus und den Chips dazu (wie baseOption: Legende selbst unsichtbar) */
function pvOption(factor = 1): EChartsCoreOption {
	const line = (name: string, color: string, value: number) => ({
		name,
		type: 'line',
		data: [
			[0, value * factor],
			[3_600_000, 2 * value * factor]
		],
		lineStyle: { color }
	});
	return {
		legend: { show: false },
		xAxis: { type: 'time' },
		yAxis: {},
		series: [line('PV', '#fa0', 1), line('Haus', '#80f', 2)]
	};
}

beforeEach(() => {
	// jsdom kann kein Canvas; ECharts misst Text dann näherungsweise
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});

afterEach(() => {
	charts.length = 0;
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

it('behält Zoom und ausgeblendete Reihen, wenn neue Daten kommen', async () => {
	const { rerender } = render(EChart, {
		option: historyOption(history, palette),
		label: 'Verlauf'
	});
	const chart = await drawn();
	chart.dispatchAction({ type: 'dataZoom', start: 20, end: 40 });
	await fireEvent.click(screen.getByRole('button', { name: 'Netz' }));
	await rerender({ option: historyOption(scaled(history, 2), palette) });
	const option = shown(chart);
	expect([option.dataZoom?.[0].start, option.dataZoom?.[0].end]).toEqual([20, 40]);
	expect(option.legend?.[0].selected?.Netz).toBe(false);
	expect(screen.getByRole('button', { name: 'Netz' })).toHaveAttribute('aria-pressed', 'false');
});

it('zeigt die Reihen als Chips, ein Tipp blendet sie aus und wieder ein', async () => {
	render(EChart, { option: pvOption(), label: 'Verlauf' });
	const chart = await drawn();
	const group = screen.getByRole('group', { name: 'Reihen von Verlauf ein- und ausblenden' });
	const chips = Array.from(group.querySelectorAll('button')).map((button) => button.textContent);
	expect(chips.map((text) => text?.trim())).toEqual(['PV', 'Haus']);
	const pv = screen.getByRole('button', { name: 'PV' });
	expect(pv).toHaveAttribute('aria-pressed', 'true');
	const setOption = vi.spyOn(chart, 'setOption');
	await fireEvent.click(pv);
	expect(pv).toHaveAttribute('aria-pressed', 'false');
	const last = setOption.mock.calls.at(-1)?.[0] as ShownOption;
	expect((last.legend as unknown as { selected: Record<string, boolean> }).selected.PV).toBe(false);
	await fireEvent.click(pv);
	expect(pv).toHaveAttribute('aria-pressed', 'true');
	expect(shown(chart).legend?.[0].selected?.PV).toBe(true);
});

it('PV bleibt ausgeblendet, nachdem eine neue option kommt', async () => {
	const { rerender } = render(EChart, { option: pvOption(1), label: 'Verlauf' });
	const chart = await drawn();
	await fireEvent.click(screen.getByRole('button', { name: 'PV' }));
	await rerender({ option: pvOption(2) });
	expect(screen.getByRole('button', { name: 'PV' })).toHaveAttribute('aria-pressed', 'false');
	expect(shown(chart).legend?.[0].selected?.PV).toBe(false);
	expect(shown(chart).legend?.[0].selected?.Haus).toBe(true);
});

it('zeigt keine Chips, wenn die Option keine Legende hat', async () => {
	render(EChart, {
		option: { xAxis: { type: 'time' }, yAxis: {}, series: [{ type: 'line', data: [[0, 1]] }] },
		label: 'Verlauf'
	});
	await drawn();
	expect(screen.queryByRole('group', { name: /^Reihen / })).toBeNull();
});

it('entfernt Reihen, die nicht mehr gewählt sind', async () => {
	const { rerender } = render(EChart, {
		option: historyOption(history, palette),
		label: 'Verlauf'
	});
	const chart = await drawn();
	await rerender({
		option: historyOption({ ...history, series: { grid: history.series.grid } }, palette)
	});
	expect(shown(chart).series).toHaveLength(1);
});

it('zeichnet bei prefers-reduced-motion ohne Animation', async () => {
	vi.stubGlobal('matchMedia', (query: string) => ({
		matches: query === '(prefers-reduced-motion: reduce)',
		media: query,
		addEventListener: () => {},
		removeEventListener: () => {}
	}));
	render(EChart, { option: historyOption(history, palette), label: 'Verlauf' });
	const chart = await drawn();
	expect(shown(chart).animation).toBe(false);
});

it('legt die Zeitachse auf Wiener Stunden und verfeinert sie beim Zoomen', async () => {
	// ein ganzer Tag (09.10. in Wien), je Viertelstunde ein Punkt
	const start = Date.parse('2026-10-08T22:00:00Z');
	const points = Array.from({ length: 97 }, (_, index): [number, number] => [
		start + index * 900_000,
		500
	]);
	const day: HistoryResponse = {
		...history,
		series: { grid: { ...history.series.grid, points } }
	};
	render(EChart, { option: historyOption(day, palette), label: 'Verlauf' });
	const chart = await drawn();
	const ticks = () => shown(chart).xAxis?.[0].axisLabel?.customValues ?? [];
	const fullHours = (values: number[]) =>
		values.every((t) => formatTime(new Date(t).toISOString()).endsWith(':00'));
	const step = (values: number[]) => (values[1] ?? 0) - (values[0] ?? 0);
	const before = ticks();
	expect(before.length).toBeGreaterThan(2);
	expect(fullHours(before)).toBe(true);
	chart.dispatchAction({ type: 'dataZoom', start: 20, end: 40 });
	await vi.waitFor(() => expect(step(ticks())).toBeLessThan(step(before)));
	expect(fullHours(ticks())).toBe(true);
});

it('benennt die Chip-Gruppe ohne Diagrammnamen allgemein', async () => {
	render(EChart, { option: pvOption(), label: '' });
	await drawn();
	expect(screen.getByRole('group', { name: 'Reihen ein- und ausblenden' })).toBeInTheDocument();
});
