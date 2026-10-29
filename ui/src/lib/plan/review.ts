// „Plan gegen Ist“: Summen für die Tabelle und das Diagramm mit geplantem und tatsächlichem SOC
import type { LineSeriesOption } from 'echarts/charts';
import type { EChartsCoreOption } from 'echarts/core';
import type { HistoryResponse, PlanReview, ReviewTotals } from '$lib/api/schemas';
import { SLOT_MS, axisTooltip, baseOption, valueAxis } from '$lib/charts/options';
import type { Palette } from '$lib/charts/palette';
import { DASH, formatEnergy, formatEur, formatSoc } from '$lib/format';
import { parseIso } from '$lib/time';

/** Die Reihe des Ist-SOC im Verlauf */
export const SOC_SERIES = 'soc.battery';

export interface ReviewRow {
	label: string;
	plan: string;
	actual: string;
	/** Ist − Plan, `–`, wenn eine Seite unbekannt ist */
	diff: string;
}

type Point = [number, number | null];

function epoch(iso: string): number {
	return parseIso(iso) ?? Number.NaN;
}

/** Gibt es eine Viertelstunde mit Plan und Ist? Nur dann zeigt die Karte etwas. */
export function hasComparison(review: PlanReview): boolean {
	return review.slots.some((slot) => slot.planned !== null && slot.actual !== null);
}

interface Quantity {
	label: string;
	value: (totals: ReviewTotals) => number | null;
	format: (value: number) => string;
}

const QUANTITIES: Quantity[] = [
	{ label: 'Netzbezug', value: (t) => t.import_kwh, format: formatEnergy },
	{ label: 'Einspeisung', value: (t) => t.export_kwh, format: formatEnergy },
	{ label: 'Netzladen', value: (t) => t.grid_charge_kwh, format: formatEnergy },
	{ label: 'Bezugskosten', value: (t) => t.import_cost_eur, format: formatEur }
];

/** Zeilen der Tabelle: Plan, Ist und die Differenz „Ist − Plan“, gerundet wie die Anzeige */
export function reviewTotals(review: PlanReview): ReviewRow[] {
	return QUANTITIES.map(({ label, value, format }) => {
		const plan = value(review.totals.planned);
		const actual = value(review.totals.actual);
		return {
			label,
			plan: plan === null ? DASH : format(plan),
			actual: actual === null ? DASH : format(actual),
			diff: plan === null || actual === null ? DASH : format(actual - plan)
		};
	});
}

/** Geplanter SOC am Ende jeder Viertelstunde von der ersten bis zur letzten mit Plan; Lücken als `null` */
function plannedSoc(review: PlanReview, from: number, to: number): Point[] {
	return review.slots
		.map((slot): Point => [epoch(slot.start) + SLOT_MS, slot.planned?.soc_end_pct ?? null])
		.filter(([end]) => end - SLOT_MS >= from && end <= to);
}

/** Ist-SOC aus dem Verlauf, nur im Zeitraum des Plans */
function actualSoc(soc: HistoryResponse | undefined, from: number, to: number): Point[] {
	const points = soc?.series[SOC_SERIES]?.points ?? [];
	return points.filter(([time]) => time >= from && time <= to);
}

export function reviewOption(
	review: PlanReview,
	soc: HistoryResponse | undefined,
	palette: Palette
): EChartsCoreOption {
	const starts = review.slots
		.filter((slot) => slot.planned !== null)
		.map((slot) => epoch(slot.start));
	const from = Math.min(...starts);
	const to = Math.max(...starts) + SLOT_MS;
	const base = baseOption(palette) as { xAxis: object; tooltip: object };
	const planned = palette.series[2] ?? palette.importPrice;
	const actual = palette.series[4] ?? palette.feedIn;
	const series: LineSeriesOption[] = [
		{
			id: 'planned',
			name: 'Geplanter SOC',
			type: 'line',
			yAxisIndex: 0,
			showSymbol: false,
			data: plannedSoc(review, from, to),
			lineStyle: { color: planned, width: 2, type: 'dashed' },
			itemStyle: { color: planned }
		},
		{
			id: 'actual',
			name: 'Ist-SOC',
			type: 'line',
			yAxisIndex: 0,
			showSymbol: false,
			data: actualSoc(soc, from, to),
			lineStyle: { color: actual, width: 2 },
			itemStyle: { color: actual }
		}
	];
	return {
		...base,
		xAxis: { ...base.xAxis, min: from, max: to },
		tooltip: {
			...base.tooltip,
			trigger: 'axis',
			formatter: axisTooltip({ 'Geplanter SOC': formatSoc, 'Ist-SOC': formatSoc })
		},
		yAxis: [{ ...valueAxis('%', palette), min: 0, max: 100 }],
		series
	};
}
