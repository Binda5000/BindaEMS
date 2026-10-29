// Diagramme des Zeitplans: Preise und Prognosen, geplanter SOC mit Netzladen- und Halten-Fenstern,
// Ladeleistung der geplanten Autos
import type { LineSeriesOption } from 'echarts/charts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Plan, PlanSlot, PlanVehicle } from '$lib/api/schemas';
import {
	SLOT_MS,
	axisTooltip,
	baseOption,
	escapeHtml,
	kw,
	nowMarkLine,
	slotEndSocPoints,
	valueAxis,
	type ValueFormat
} from '$lib/charts/options';
import type { Palette } from '$lib/charts/palette';
import { NBSP, expectedLabel, formatCt, formatNumber, formatTime } from '$lib/format';
import { parseIso } from '$lib/time';
import { INTENT_LABELS, intentLabel } from './model';

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

/**
 * Zeitachse vom Slot von jetzt (nicht vor dem ersten Slot) bis zum Ende des Horizonts; die Linie
 * „jetzt“ nur, wenn es darin liegt
 */
function timeAxis(plan: Plan, nowIso: string, palette: Palette) {
	const base = baseOption(palette) as { xAxis: object };
	const first = plan.slots[0];
	const from = first ? epoch(first.start) : undefined;
	const to = parseIso(plan.horizon_end) ?? undefined;
	const now = parseIso(nowIso);
	const inside = now !== null && from !== undefined && to !== undefined && now >= from && now <= to;
	const start =
		inside && from !== undefined ? Math.max(from, Math.floor(now / SLOT_MS) * SLOT_MS) : from;
	return {
		xAxis: { ...base.xAxis, min: start, max: to },
		now: inside ? now : null
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
			markLine: nowMarkLine(now, palette)
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
	const base = baseOption(palette) as { legend: object; tooltip: object };
	return {
		...base,
		xAxis,
		legend: {
			...base.legend,
			// ohne geschätzte Preise bleibt die Reihe aus der Legende
			data: series
				.map((s) => s.name)
				.filter((name) => name !== 'Bezugspreis geschätzt' || slots.some((s) => s.estimated))
		},
		tooltip: {
			...base.tooltip,
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

/**
 * Der Slot, der genau bei `time` endet, mit Beschriftung seiner Absicht und Zeitspanne (ISO),
 * sonst `null`. Der geplante SOC gilt am Ende des Slots (`slotEndSocPoints`): der Punkt dort ist
 * das Ergebnis dieses Slots.
 */
export function intentEndingAt(
	slots: PlanSlot[],
	time: number
): { from: string; to: string; label: string } | null {
	const slot = slots.find((item) => epoch(item.start) + SLOT_MS === time);
	return slot
		? {
				from: new Date(epoch(slot.start)).toISOString(),
				to: new Date(time).toISOString(),
				label: intentLabel(slot.intent, slot.grid_charge_w)
			}
		: null;
}

export function planSocOption(plan: Plan, nowIso: string, palette: Palette): EChartsCoreOption {
	const { xAxis, now } = timeAxis(plan, nowIso, palette);
	const colors = { NETZLADEN: palette.importPrice, HALTEN: palette.pv };
	// die Flächen tragen keinen Text (kurze Fenster überlagerten sich auf dem Handy); die Legende
	// und der Tooltip nennen die Absicht
	const areas = windows(plan.slots).map((window) => [
		{
			xAxis: window.start,
			itemStyle: { color: colors[window.intent], opacity: 0.18 },
			label: { show: false }
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
			data: slotEndSocPoints(plan.slots),
			lineStyle: { color: palette.series[2] ?? palette.importPrice, width: 2 },
			itemStyle: { color: palette.series[2] ?? palette.importPrice },
			markArea: { silent: true, data: areas } as LineSeriesOption['markArea'],
			markLine: nowMarkLine(now, palette)
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
	// Legendeneinträge für die Flächen: Reihen ohne Daten, nur für Absichten, die der Plan hat
	// (das Muster in der Legende zeigt nur den Farbton; ein Klick darauf blendet die Fläche nicht aus, sie hängt an der SOC-Reihe)
	for (const intent of ['NETZLADEN', 'HALTEN'] as const) {
		if (!plan.slots.some((slot) => slot.intent === intent)) continue;
		const name = INTENT_LABELS[intent];
		series.push({
			id: `legend_${name.toLowerCase()}`,
			name,
			type: 'line',
			data: [],
			itemStyle: { color: colors[intent] },
			lineStyle: { width: 0 },
			areaStyle: { color: colors[intent], opacity: 0.18 }
		});
	}
	const base = baseOption(palette) as { legend: object; tooltip: object };
	const format = axisTooltip({
		'Geplanter SOC': socFormat,
		Reserve: socFormat,
		Netzladen: null,
		Halten: null
	});
	return {
		...base,
		xAxis,
		legend: { ...base.legend, data: series.map((s) => s.name) },
		tooltip: {
			...base.tooltip,
			trigger: 'axis',
			formatter: (params: unknown): string => {
				const html = format(params);
				// die Zeit des Punktes: die der SOC-Reihe, sonst der ersten Reihe mit Wert
				const items = (Array.isArray(params) ? params : [params]) as {
					seriesName?: string;
					value?: unknown;
				}[];
				const item = items.find((i) => i.seriesName === 'Geplanter SOC') ?? items[0];
				const point = item && Array.isArray(item.value) ? item.value[0] : undefined;
				const slot = typeof point === 'number' ? intentEndingAt(plan.slots, point) : null;
				return slot === null
					? html
					: `${html}<br>Akku ${formatTime(slot.from)}\u2013${formatTime(slot.to)}: <b>${escapeHtml(slot.label)}</b>`;
			}
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

/** Name der Reihe: erwartete Autos tragen ihre Ankunft, „{Name} erwartet ab {HH:MM}“ */
function carName(car: PlanVehicle): string {
	return car.expected && car.arrival_at !== null
		? expectedLabel(car.name, car.arrival_at)
		: car.name;
}

/**
 * Diagramm „Autos“: je geplantem Auto die Ladeleistung des Plans in kW (ohne Laden 0 kW). Erwartete
 * Autos sind gestrichelt und ohne Fläche gezeichnet.
 */
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
			name: carName(car),
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(plan.slots, (slot) => (slot.ev_w[car.key] ?? 0) / 1000),
			lineStyle: car.expected ? { color, width: 2, type: 'dashed' } : { color, width: 2 },
			itemStyle: { color },
			areaStyle: car.expected ? undefined : { color, opacity: 0.15 },
			markLine: index === 0 ? nowMarkLine(now, palette) : undefined
		};
	});
	const base = baseOption(palette) as { tooltip: object };
	return {
		...base,
		xAxis,
		tooltip: {
			...base.tooltip,
			trigger: 'axis',
			formatter: axisTooltip(Object.fromEntries(plan.vehicles.map((car) => [carName(car), kw])))
		},
		yAxis: [{ ...valueAxis('kW', palette), min: 0 }],
		series
	};
}
