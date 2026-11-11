// Diagramme der Seite „PV-Anlagen“: Tageskurve je Eintrag und Ertrag je Tag bzw. Monat
import type { BarSeriesOption, LineSeriesOption } from 'echarts/charts';
import type { EChartsCoreOption } from 'echarts/core';
import type { PvDay, PvPeriod, PvSource } from '$lib/api/schemas';
import { baseOption, escapeHtml, nowMarkLine, timeLabel, valueAxis } from '$lib/charts/options';
import type { Palette } from '$lib/charts/palette';
import { DASH, NBSP, formatDay, formatPower, weekdayOfDate } from '$lib/format';
import { MONTHS } from '$lib/stats/period';
import { localDayRange, parseIso } from '$lib/time';
import { formatKwh, formatPerKwp } from './tree';

export type PvView = 'systems' | 'strings';
export type PvUnit = 'kwh' | 'kwp';

const STEP_MS = 300_000; // 5-Minuten-Mittel der Tageskurve

type Point = [number, number | null];

function sorted(sources: PvSource[]): PvSource[] {
	return [...sources].sort(
		(a, b) =>
			a.sort - b.sort || a.name.localeCompare(b.name, 'de', { sensitivity: 'base' }) || a.id - b.id
	);
}

/** Strings: Einträge mit einer vorhandenen Anlage als `parent_id` */
function isString(source: PvSource, ids: Set<number>): boolean {
	return source.parent_id !== null && ids.has(source.parent_id);
}

export function hasStringEntries(sources: PvSource[]): boolean {
	const ids = new Set(sources.map((source) => source.id));
	return sources.some((source) => isString(source, ids));
}

/**
 * Einträge im Diagramm: Anlagen oder Strings; bei kWh/kWp nur die mit kWp, die anderen stehen in
 * `withoutKwp`.
 */
export function periodEntries(
	sources: PvSource[],
	view: PvView,
	unit: PvUnit
): { shown: PvSource[]; withoutKwp: PvSource[] } {
	const ids = new Set(sources.map((source) => source.id));
	const wanted = sorted(sources).filter((source) => isString(source, ids) === (view === 'strings'));
	if (unit === 'kwh') return { shown: wanted, withoutKwp: [] };
	return {
		shown: wanted.filter((source) => source.kwp !== null),
		withoutKwp: wanted.filter((source) => source.kwp === null)
	};
}

/** Beschriftung eines Balkens: Wochentag („Mo“), Tag des Monats („8“), Monat („Nov.“) */
export function bucketLabel(period: PvPeriod['period'], start: string): string {
	if (period === 'year') return MONTHS[Number(start.slice(5, 7)) - 1]?.short ?? start;
	if (period === 'week') return weekdayOfDate(start);
	return String(Number(start.slice(8, 10)));
}

/** Datum im Tooltip: „Mo., 02.11.“ bzw. „November 2026“ */
function bucketTitle(period: PvPeriod['period'], start: string): string {
	if (period !== 'year') return formatDay(start);
	return `${MONTHS[Number(start.slice(5, 7)) - 1]?.long ?? ''} ${start.slice(0, 4)}`;
}

function shortDate(day: string): string {
	return `${day.slice(8, 10)}.${day.slice(5, 7)}.`;
}

export interface PeriodTable {
	labels: string[];
	titles: string[];
	rows: { source: PvSource; values: (number | null)[] }[];
}

/** Werte je Eintrag und Bucket in kWh oder kWh/kWp; ohne Wert `null` (Lücke, kein 0-Balken) */
export function periodTable(period: PvPeriod, shown: PvSource[], unit: PvUnit): PeriodTable {
	const energy = new Map(period.sources.map((source) => [source.id, source.kwh]));
	return {
		labels: period.buckets.map((bucket) => bucketLabel(period.period, bucket.start)),
		titles: period.buckets.map((bucket) => bucketTitle(period.period, bucket.start)),
		rows: shown.map((source) => ({
			source,
			values: period.buckets.map((_, index) => {
				const kwh = energy.get(source.id)?.[index] ?? null;
				if (kwh === null || unit === 'kwh') return kwh;
				return source.kwp ? Math.round((kwh / source.kwp) * 1000) / 1000 : null;
			})
		}))
	};
}

export function formatPeriodValue(value: number | null | undefined, unit: PvUnit): string {
	if (unit === 'kwh') return formatKwh(value);
	return typeof value === 'number' ? `${formatPerKwp(value)}${NBSP}kWh/kWp` : DASH;
}

/**
 * Buckets vor dem ersten Wert (bis heute): dort gibt es noch keine Daten, etwa vor der
 * Inbetriebnahme. Liefert den Bereich der Indizes und den Text, sonst `null`.
 */
export function noDataRange(
	period: PvPeriod,
	shown: PvSource[],
	today: string
): { from: number; to: number; text: string } | null {
	const ids = new Set(shown.map((source) => source.id));
	const sources = period.sources.filter((source) => ids.has(source.id));
	if (sources.length === 0) return null;
	let to = -1;
	for (const [index, bucket] of period.buckets.entries()) {
		if (bucket.start > today) break;
		if (sources.some((source) => source.kwh[index] !== null)) break;
		to = index;
	}
	if (to < 0) return null;
	const known = sources
		.map((source) => source.first_known)
		.filter((day): day is string => day !== null)
		.sort();
	const first = known[0];
	return {
		from: 0,
		to,
		text: first ? `noch keine Daten (Werte ab ${shortDate(first)})` : 'noch keine Daten'
	};
}

function periodTooltip(table: PeriodTable, unit: PvUnit) {
	return (params: unknown): string => {
		const items = (Array.isArray(params) ? params : [params]) as {
			marker?: string;
			seriesName?: string;
			dataIndex?: number;
			value?: unknown;
		}[];
		const index = items[0]?.dataIndex ?? 0;
		const lines = items.map((item) => {
			const value = typeof item.value === 'number' ? item.value : null;
			const name = escapeHtml(item.seriesName ?? '');
			return `${item.marker ?? ''}${name} <b>${formatPeriodValue(value, unit)}</b>`;
		});
		return [escapeHtml(table.titles[index] ?? ''), ...lines].join('<br>');
	};
}

/** Gruppierte Balken je Tag (Woche, Monat) bzw. Monat (Jahr), je Eintrag in seiner Farbe */
export function periodOption(
	period: PvPeriod,
	shown: PvSource[],
	unit: PvUnit,
	today: string,
	palette: Palette
): EChartsCoreOption {
	const table = periodTable(period, shown, unit);
	const empty = noDataRange(period, shown, today);
	const series: BarSeriesOption[] = table.rows.map((row, index) => ({
		name: row.source.name,
		type: 'bar',
		data: row.values,
		itemStyle: { color: row.source.color },
		barMaxWidth: 18,
		...(index === 0 && empty
			? {
					markArea: {
						silent: true,
						itemStyle: { color: palette.grid, opacity: 0.25 },
						label: {
							color: palette.muted,
							position: 'insideTop',
							overflow: 'break',
							fontSize: 11
						},
						data: [[{ xAxis: empty.from, name: empty.text }, { xAxis: empty.to }]]
					}
				}
			: {})
	}));
	const base = baseOption(palette) as { tooltip: object };
	return {
		...base,
		tooltip: { ...base.tooltip, trigger: 'axis', formatter: periodTooltip(table, unit) },
		xAxis: {
			type: 'category',
			data: table.labels,
			axisLabel: { color: palette.muted, hideOverlap: true },
			axisLine: { lineStyle: { color: palette.grid } },
			axisTick: { alignWithLabel: true }
		},
		yAxis: {
			...valueAxis(unit === 'kwh' ? 'kWh' : 'kWh/kWp', palette),
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series
	};
}

/** Lücke, wo ein 5-Minuten-Wert fehlt (sonst zöge die Linie über die Lücke) */
function withGaps(points: [number, number][]): Point[] {
	const result: Point[] = [];
	let previous: number | null = null;
	for (const [time, watts] of points) {
		if (previous !== null && time - previous > 1.5 * STEP_MS)
			result.push([previous + STEP_MS, null]);
		result.push([time, watts / 1000]);
		previous = time;
	}
	return result;
}

function dayTooltip(params: unknown): string {
	const items = (Array.isArray(params) ? params : [params]) as {
		marker?: string;
		seriesName?: string;
		value?: unknown;
	}[];
	const named = items.filter((item) => item.seriesName);
	const first = named[0]?.value;
	const time = Array.isArray(first) && typeof first[0] === 'number' ? timeLabel(first[0]) : '';
	const lines = named.map((item) => {
		const kw = Array.isArray(item.value) ? item.value[1] : null;
		const text = typeof kw === 'number' ? formatPower(kw * 1000) : DASH;
		return `${item.marker ?? ''}${escapeHtml(item.seriesName ?? '')} <b>${text}</b>`;
	});
	return [time, ...lines].join('<br>');
}

/**
 * Leistungskurve des Tages: eine Linie je Eintrag in seiner Farbe, Anlagen mit Strings
 * gestrichelt (AC hinter dem Wechselrichter, die Strings DC davor). Die Achse reicht über den
 * ganzen Tag; `now` zeichnet die Linie „jetzt“.
 */
export function dayOption(
	day: PvDay,
	sources: PvSource[],
	palette: Palette,
	now: number | null
): EChartsCoreOption {
	const points = new Map(day.series.map((entry) => [entry.id, entry.points]));
	const ids = new Set(sources.map((source) => source.id));
	const parents = new Set(
		sources.filter((source) => isString(source, ids)).map((source) => source.parent_id)
	);
	const range = localDayRange(day.date);
	const start = parseIso(range.from) ?? 0;
	const end = parseIso(range.to) ?? 0;
	const lines: LineSeriesOption[] = sorted(sources).map((source) => ({
		name: source.name,
		type: 'line',
		showSymbol: false,
		connectNulls: false,
		data: withGaps(points.get(source.id) ?? []),
		lineStyle: {
			color: source.color,
			width: 2,
			type: parents.has(source.id) ? 'dashed' : 'solid'
		},
		itemStyle: { color: source.color }
	}));
	// unsichtbare Reihe ohne Namen: hält die Achse und ihre Marken über den ganzen Tag
	const frame: LineSeriesOption = {
		type: 'line',
		silent: true,
		showSymbol: false,
		data: [
			[start, null],
			[end, null]
		],
		markLine: now !== null && now >= start && now < end ? nowMarkLine(now, palette) : undefined
	};
	const base = baseOption(palette) as { tooltip: object; xAxis: object };
	return {
		...base,
		tooltip: { ...base.tooltip, trigger: 'axis', formatter: dayTooltip },
		xAxis: { ...base.xAxis, min: start, max: end },
		yAxis: {
			...valueAxis('kW', palette),
			min: 0,
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series: [...lines, frame]
	};
}

/** Einträge mit leerer Kurve und ihr Grund, etwa „Süd: HA-Datenbank nicht eingerichtet“ */
export function dayNotes(day: PvDay, sources: PvSource[]): string[] {
	const names = new Map(sources.map((source) => [source.id, source.name]));
	return day.series
		.filter((entry) => entry.points.length === 0 && entry.note)
		.map((entry) => `${names.get(entry.id) ?? `Eintrag ${entry.id}`}: ${entry.note}`);
}
