// Diagramme des Zeitplans: Preise und Prognosen, geplanter SOC mit Netzladen- und Halten-Fenstern,
// Ladeleistung der geplanten Autos
import type { LineSeriesOption } from 'echarts/charts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Plan, PlanSlot } from '$lib/api/schemas';
import {
	SLOT_MS,
	axisTooltip,
	baseOption,
	kw,
	valueAxis,
	type ValueFormat
} from '$lib/charts/options';
import type { Palette } from '$lib/charts/palette';
import { NBSP, formatCt, formatNumber } from '$lib/format';
import { parseIso } from '$lib/time';
import { INTENT_LABELS } from './model';

type Point = [number, number | null];

function epoch(iso: string): number {
	return parseIso(iso) ?? Number.NaN;
}

/** Stufen: ein Punkt je Slot und ein Schlusspunkt am Ende des letzten Slots */
function steps(slots: PlanSlot[], value: (slot: PlanSlot) => number | null): Point[] {
	const points: Point[] = slots.map((slot) => [epoch(slot.start), value(slot)]);
	const last = slots.at(-1);
	if (last) points.push([epoch(last.start) + SLOT_MS, value(last)]);
	return points;
}

/** Zeitachse vom ersten Slot bis zum Ende des Horizonts; „jetzt“ nur, wenn es darin liegt */
function timeAxis(plan: Plan, nowIso: string, palette: Palette) {
	const base = baseOption(palette) as { xAxis: object };
	const first = plan.slots[0];
	const from = first ? epoch(first.start) : undefined;
	const to = parseIso(plan.horizon_end) ?? undefined;
	const now = parseIso(nowIso);
	const inside = now !== null && from !== undefined && to !== undefined && now >= from && now <= to;
	return {
		xAxis: { ...base.xAxis, min: from, max: to },
		now: inside ? now : null
	};
}

function nowLine(now: number | null, palette: Palette) {
	return now === null
		? undefined
		: {
				symbol: 'none',
				silent: true,
				lineStyle: { color: palette.muted, type: 'solid' as const },
				label: { formatter: 'jetzt', color: palette.muted },
				data: [{ xAxis: now }]
			};
}

export function planPriceOption(plan: Plan, nowIso: string, palette: Palette): EChartsCoreOption {
	const { xAxis, now } = timeAxis(plan, nowIso, palette);
	const slots = plan.slots;
	const series: LineSeriesOption[] = [
		{
			id: 'import',
			name: 'Bezugspreis',
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(slots, (slot) => (slot.estimated ? null : slot.import_ct)),
			lineStyle: { color: palette.importPrice, width: 2 },
			itemStyle: { color: palette.importPrice },
			markLine: nowLine(now, palette)
		},
		{
			id: 'import_estimated',
			name: 'Bezugspreis geschätzt',
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(slots, (slot) => (slot.estimated ? slot.import_ct : null)),
			lineStyle: { color: palette.importPrice, width: 2, type: 'dashed' },
			itemStyle: { color: palette.importPrice }
		},
		{
			id: 'pv',
			name: 'PV-Prognose',
			type: 'line',
			yAxisIndex: 1,
			smooth: true,
			showSymbol: false,
			data: slots.map((slot): Point => [epoch(slot.start), slot.pv_w / 1000]),
			lineStyle: { color: palette.pv },
			itemStyle: { color: palette.pv },
			areaStyle: { color: palette.pv, opacity: 0.2 }
		},
		{
			id: 'load',
			name: 'Lastprognose',
			type: 'line',
			yAxisIndex: 1,
			smooth: true,
			showSymbol: false,
			data: slots.map((slot): Point => [epoch(slot.start), slot.load_w / 1000]),
			lineStyle: { color: palette.load, type: 'dashed' },
			itemStyle: { color: palette.load }
		}
	];
	const base = baseOption(palette) as { legend: object };
	return {
		...baseOption(palette),
		xAxis,
		legend: {
			...base.legend,
			// ohne geschätzte Preise bleibt die Reihe aus der Legende
			data: series
				.map((s) => s.name)
				.filter((name) => name !== 'Bezugspreis geschätzt' || slots.some((s) => s.estimated))
		},
		tooltip: {
			trigger: 'axis',
			formatter: axisTooltip({
				Bezugspreis: formatCt,
				'Bezugspreis geschätzt': formatCt,
				'PV-Prognose': kw,
				Lastprognose: kw
			})
		},
		yAxis: [valueAxis('ct/kWh', palette), valueAxis('kW', palette)],
		series
	};
}

interface Window {
	intent: 'NETZLADEN' | 'HALTEN';
	start: number;
	end: number;
}

/** Fenster, in denen der Akku Netzladen oder Halten soll (aufeinanderfolgende Slots derselben Absicht) */
function windows(slots: PlanSlot[]): Window[] {
	const result: Window[] = [];
	for (const slot of slots) {
		if (slot.intent === 'EIGENVERBRAUCH') continue;
		const start = epoch(slot.start);
		const last = result.at(-1);
		if (last && last.intent === slot.intent && last.end === start) last.end = start + SLOT_MS;
		else result.push({ intent: slot.intent, start, end: start + SLOT_MS });
	}
	return result;
}

const socFormat: ValueFormat = (value) => `${formatNumber(value, 0)}${NBSP}%`;

export function planSocOption(plan: Plan, nowIso: string, palette: Palette): EChartsCoreOption {
	const { xAxis, now } = timeAxis(plan, nowIso, palette);
	const colors = { NETZLADEN: palette.importPrice, HALTEN: palette.pv };
	const areas = windows(plan.slots).map((window) => [
		{
			name: INTENT_LABELS[window.intent],
			xAxis: window.start,
			itemStyle: { color: colors[window.intent], opacity: 0.18 },
			label: { color: palette.text, position: 'insideTop' }
		},
		{ xAxis: window.end }
	]);
	const first = plan.slots[0];
	const from = first ? epoch(first.start) : Number.NaN;
	const to = parseIso(plan.horizon_end) ?? Number.NaN;
	const series: LineSeriesOption[] = [
		{
			id: 'soc',
			name: 'Geplanter SOC',
			type: 'line',
			yAxisIndex: 0,
			showSymbol: false,
			// der SOC gilt am Ende des Slots
			data: plan.slots.map((slot): Point => [epoch(slot.start) + SLOT_MS, slot.soc_pct]),
			lineStyle: { color: palette.series[2] ?? palette.importPrice, width: 2 },
			itemStyle: { color: palette.series[2] ?? palette.importPrice },
			markArea: { silent: true, data: areas } as LineSeriesOption['markArea'],
			markLine: nowLine(now, palette)
		},
		{
			id: 'reserve',
			name: 'Reserve',
			type: 'line',
			yAxisIndex: 0,
			showSymbol: false,
			silent: true,
			data: [
				[from, plan.reserve_pct],
				[to, plan.reserve_pct]
			] as Point[],
			lineStyle: { color: palette.muted, type: 'dashed' },
			itemStyle: { color: palette.muted }
		}
	];
	return {
		...baseOption(palette),
		xAxis,
		tooltip: {
			trigger: 'axis',
			formatter: axisTooltip({ 'Geplanter SOC': socFormat, Reserve: socFormat })
		},
		yAxis: [
			{
				...valueAxis('%', palette),
				min: 0,
				max: 100,
				splitLine: { show: true, lineStyle: { color: palette.grid } }
			}
		],
		series
	};
}

/** Diagramm „Autos“: je geplantem Auto die Ladeleistung des Plans in kW (ohne Laden 0 kW) */
export function planVehiclesOption(
	plan: Plan,
	nowIso: string,
	palette: Palette
): EChartsCoreOption {
	const { xAxis, now } = timeAxis(plan, nowIso, palette);
	const series: LineSeriesOption[] = plan.vehicles.map((car, index) => {
		const color = palette.series[index % palette.series.length] ?? palette.load;
		return {
			id: `ev:${car.key}`,
			name: car.name,
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(plan.slots, (slot) => (slot.ev_w[car.key] ?? 0) / 1000),
			lineStyle: { color, width: 2 },
			itemStyle: { color },
			areaStyle: { color, opacity: 0.15 },
			markLine: index === 0 ? nowLine(now, palette) : undefined
		};
	});
	return {
		...baseOption(palette),
		xAxis,
		tooltip: {
			trigger: 'axis',
			formatter: axisTooltip(Object.fromEntries(plan.vehicles.map((car) => [car.name, kw])))
		},
		yAxis: [{ ...valueAxis('kW', palette), min: 0 }],
		series
	};
}
