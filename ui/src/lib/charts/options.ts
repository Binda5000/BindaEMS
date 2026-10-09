// Optionen für ECharts: Zeitachse in Wien, Farben nur aus der Palette
import type { LineSeriesOption } from 'echarts/charts';
import type { EChartsCoreOption } from 'echarts/core';
import type { ForecastSlot, HistoryResponse, PriceSlot } from '$lib/api/schemas';
import { DASH, NBSP, formatCt, formatDay, formatNumber, formatTime } from '$lib/format';
import { parseIso, todayVienna } from '$lib/time';
import type { Palette } from './palette';

export const SLOT_MS = 900_000;

type Point = [number, number | null];
type ValueFormat = (value: number) => string;

function epoch(iso: string): number {
	return parseIso(iso) ?? Number.NaN;
}

/** Beschriftung der Zeitachse: Uhrzeit in Wien, um Mitternacht der Tag */
export function timeLabel(value: number): string {
	const date = new Date(value);
	const time = formatTime(date.toISOString());
	return time === '00:00' ? formatDay(todayVienna(date)) : time;
}

/** Stufen: ein Punkt je Slot und ein Schlusspunkt am Ende des letzten Slots */
function steps(slots: PriceSlot[], value: (slot: PriceSlot) => number | null): Point[] {
	const points: Point[] = slots.map((slot) => [epoch(slot.start), value(slot)]);
	const last = slots.at(-1);
	if (last) points.push([epoch(last.start) + SLOT_MS, value(last)]);
	return points;
}

const kw: ValueFormat = (value) => `${formatNumber(value, 2)}${NBSP}kW`;

/** Tooltip mit der Zeit in Wien und den Werten je Reihe in ihrer Einheit */
export function axisTooltip(formats: Record<string, ValueFormat>) {
	return (params: unknown): string => {
		const items = (Array.isArray(params) ? params : [params]) as {
			marker?: string;
			seriesName?: string;
			value?: unknown;
		}[];
		const first = items[0]?.value;
		const time = Array.isArray(first) && typeof first[0] === 'number' ? timeLabel(first[0]) : '';
		const lines = items.map((item) => {
			const value = Array.isArray(item.value) ? item.value[1] : item.value;
			const format = formats[item.seriesName ?? ''] ?? ((v: number) => formatNumber(v, 1));
			const text = typeof value === 'number' ? format(value) : DASH;
			return `${item.marker ?? ''}${item.seriesName ?? ''}: <b>${text}</b>`;
		});
		return [time, ...lines].join('<br>');
	};
}

export function baseOption(palette: Palette): EChartsCoreOption {
	return {
		backgroundColor: 'transparent',
		textStyle: { color: palette.text },
		grid: { left: 8, right: 8, top: 40, bottom: 8, containLabel: true },
		legend: { top: 0, textStyle: { color: palette.text } },
		xAxis: {
			type: 'time',
			axisLabel: { color: palette.muted, formatter: timeLabel, hideOverlap: true },
			axisLine: { lineStyle: { color: palette.grid } },
			splitLine: { show: false }
		}
	};
}

function valueAxis(name: string, palette: Palette, show = true) {
	return {
		type: 'value',
		name,
		show,
		nameTextStyle: { color: palette.muted },
		axisLabel: { color: palette.muted },
		splitLine: { show: name === 'ct/kWh' || name === '', lineStyle: { color: palette.grid } }
	};
}

export function priceForecastOption(
	prices: PriceSlot[],
	forecast: ForecastSlot[],
	nowIso: string,
	palette: Palette
): EChartsCoreOption {
	const now = parseIso(nowIso);
	const series: LineSeriesOption[] = [
		{
			name: 'Bezugspreis',
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(prices, (slot) => slot.import_gross_ct),
			lineStyle: { color: palette.importPrice, width: 2 },
			itemStyle: { color: palette.importPrice },
			markLine:
				now === null
					? undefined
					: {
							symbol: 'none',
							silent: true,
							lineStyle: { color: palette.muted, type: 'solid' },
							label: { formatter: 'jetzt', color: palette.muted },
							data: [{ xAxis: now }]
						}
		}
	];
	if (prices.some((slot) => slot.feed_in_ct !== null)) {
		series.push({
			name: 'Einspeisung',
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(prices, (slot) => slot.feed_in_ct),
			lineStyle: { color: palette.feedIn, type: 'dashed' },
			itemStyle: { color: palette.feedIn }
		});
	}
	if (forecast.length > 0) {
		series.push({
			name: 'PV-Prognose',
			type: 'line',
			yAxisIndex: 1,
			smooth: true,
			showSymbol: false,
			data: forecast.map((slot): Point => [epoch(slot.start), slot.p50_w / 1000]),
			lineStyle: { color: palette.pv },
			itemStyle: { color: palette.pv },
			areaStyle: { color: palette.pv, opacity: 0.2 }
		});
	}
	return {
		...baseOption(palette),
		tooltip: {
			trigger: 'axis',
			formatter: axisTooltip({ Bezugspreis: formatCt, Einspeisung: formatCt, 'PV-Prognose': kw })
		},
		yAxis: [valueAxis('ct/kWh', palette), valueAxis('kW', palette, forecast.length > 0)],
		series
	};
}

/** Üblicher Punktabstand einer Reihe (unterer Median der Abstände) */
function typicalInterval(points: [number, number][]): number {
	const gaps = points.slice(1).map(([time], index) => time - points[index][0]);
	gaps.sort((a, b) => a - b);
	return gaps.length > 0 ? gaps[Math.floor((gaps.length - 1) / 2)] : 0;
}

/**
 * Lücke, wenn zwei Punkte mehr als 1,5 Raster auseinander liegen (fehlende Messwerte). Das
 * Raster ist der Schritt der Abfrage oder, wenn die Reihe gröber vorliegt (Preis und Prognose
 * je Viertelstunde), ihr üblicher Punktabstand.
 */
function withGaps(points: [number, number][], stepMs: number, scale: number): Point[] {
	const interval = Math.max(stepMs, typicalInterval(points));
	const result: Point[] = [];
	let previous: number | null = null;
	for (const [time, value] of points) {
		if (previous !== null && time - previous > 1.5 * interval) {
			result.push([previous + interval, null]);
		}
		result.push([time, scale === 1 ? value : value / scale]);
		previous = time;
	}
	return result;
}

function unitFormat(unit: string): ValueFormat {
	if (unit === '%') return (value) => `${formatNumber(value, 0)}${NBSP}%`;
	if (unit === 'ct/kWh') return formatCt;
	return (value) => `${formatNumber(value, 2)}${NBSP}${unit}`;
}

export function historyOption(data: HistoryResponse, palette: Palette): EChartsCoreOption {
	const units: string[] = [];
	const formats: Record<string, ValueFormat> = {};
	const series: LineSeriesOption[] = Object.values(data.series).map((entry, index) => {
		const unit = entry.unit === 'W' ? 'kW' : entry.unit; // Leistung in kW
		if (!units.includes(unit)) units.push(unit);
		formats[entry.label] = unitFormat(unit);
		const color = palette.series[index % palette.series.length];
		return {
			name: entry.label,
			type: 'line',
			yAxisIndex: units.indexOf(unit),
			showSymbol: false,
			data: withGaps(entry.points, data.step_s * 1000, entry.unit === 'W' ? 1000 : 1),
			lineStyle: { color, width: 1.5 },
			itemStyle: { color }
		};
	});
	return {
		...baseOption(palette),
		grid: { left: 8, right: 8, top: 40, bottom: 44, containLabel: true },
		tooltip: { trigger: 'axis', formatter: axisTooltip(formats) },
		dataZoom: [
			{ type: 'inside' },
			{ type: 'slider', height: 20, bottom: 8, textStyle: { color: palette.muted } }
		],
		yAxis: units.map((unit, index) => ({
			...valueAxis(unit, palette),
			position: index % 2 === 0 ? 'left' : 'right',
			offset: Math.floor(index / 2) * 48,
			splitLine: { show: index === 0, lineStyle: { color: palette.grid } }
		})),
		series
	};
}
