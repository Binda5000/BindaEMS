// Optionen für ECharts: Zeitachse in Wien, Farben nur aus der Palette
import type { BarSeriesOption, LineSeriesOption } from 'echarts/charts';
import type { EChartsCoreOption } from 'echarts/core';
import type {
	ForecastSlot,
	HistoryResponse,
	LoadForecast,
	Plan,
	PlanSlot,
	PriceSlot,
	Stats
} from '$lib/api/schemas';
import {
	DASH,
	NBSP,
	formatCt,
	formatDay,
	formatEnergy,
	formatEur,
	formatNumber,
	formatSoc,
	formatTime
} from '$lib/format';
import type { DailyPoint } from '$lib/forecast/view';
import { bucketLabels, vehicleLabel } from '$lib/stats/view';
import { parseIso, todayVienna } from '$lib/time';
import type { Palette } from './palette';

export const SLOT_MS = 900_000;

type Point = [number, number | null];
/** `point` ist der ganze Datenpunkt `[Zeit, Wert, …]`, falls eine Reihe mehr als einen Wert trägt */
export type ValueFormat = (value: number, point?: unknown[]) => string;

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

export const kw: ValueFormat = (value) => `${formatNumber(value, 2)}${NBSP}kW`;

const HTML_ESCAPES: Record<string, string> = {
	'&': '&amp;',
	'<': '&lt;',
	'>': '&gt;',
	'"': '&quot;',
	"'": '&#39;'
};

/** Text für HTML: der Tooltip ist HTML, Reihennamen kommen aus der Konfiguration */
export function escapeHtml(text: string): string {
	return text.replace(/[&<>"']/g, (char) => HTML_ESCAPES[char] ?? char);
}

/**
 * Tooltip mit der Zeit in Wien und den Werten je Reihe in ihrer Einheit. Reihen mit `null` als
 * Format bleiben unsichtbar (Hilfsreihen).
 */
export function axisTooltip(formats: Record<string, ValueFormat | null>) {
	return (params: unknown): string => {
		const items = (Array.isArray(params) ? params : [params]) as {
			marker?: string;
			seriesName?: string;
			value?: unknown;
		}[];
		const first = items[0]?.value;
		const time = Array.isArray(first) && typeof first[0] === 'number' ? timeLabel(first[0]) : '';
		const shown = items.filter((item) => formats[item.seriesName ?? ''] !== null);
		const lines = shown.map((item) => {
			const point = Array.isArray(item.value) ? item.value : undefined;
			const value = point ? point[1] : item.value;
			const format = formats[item.seriesName ?? ''] ?? ((v: number) => formatNumber(v, 1));
			const text = typeof value === 'number' ? format(value, point) : DASH;
			return `${item.marker ?? ''}${escapeHtml(item.seriesName ?? '')}: <b>${text}</b>`;
		});
		return [time, ...lines].join('<br>');
	};
}

export function baseOption(palette: Palette): EChartsCoreOption {
	return {
		backgroundColor: 'transparent',
		textStyle: { color: palette.text },
		grid: { left: 8, right: 8, top: 40, bottom: 8, containLabel: true },
		// eine Zeile, bei Platzmangel blättern statt umbrechen (und das Diagramm zu überdecken)
		legend: {
			type: 'scroll',
			top: 0,
			textStyle: { color: palette.text },
			pageTextStyle: { color: palette.muted },
			pageIconColor: palette.text,
			pageIconInactiveColor: palette.muted
		},
		// der Tooltip bleibt im Diagramm, auch am Rand eines schmalen Handys
		tooltip: { confine: true },
		xAxis: {
			type: 'time',
			axisLabel: { color: palette.muted, formatter: timeLabel, hideOverlap: true },
			axisLine: { lineStyle: { color: palette.grid } },
			splitLine: { show: false }
		}
	};
}

/**
 * Linie „jetzt“; die Beschriftung sitzt senkrecht innen am oberen Ende der Linie. Die Linie läuft
 * nach oben, der Text ist gedreht (liest von unten nach oben): `insideEndTop` setzt ihn links der
 * Linie, auf die Achsenwerte (die Zeitachse beginnt im Zeitplan an der Linie), `insideEndBottom`
 * rechts davon, ins Diagramm. Ohne Zeitpunkt keine Linie.
 */
export function nowMarkLine(now: number | null, palette: Palette) {
	return now === null
		? undefined
		: {
				symbol: 'none',
				silent: true,
				lineStyle: { color: palette.muted, type: 'solid' as const },
				label: { formatter: 'jetzt', color: palette.muted, position: 'insideEndBottom' as const },
				data: [{ xAxis: now }]
			};
}

export function valueAxis(name: string, palette: Palette, show = true) {
	return {
		type: 'value',
		name,
		show,
		nameTextStyle: { color: palette.muted },
		axisLabel: { color: palette.muted },
		splitLine: { show: name === 'ct/kWh' || name === '', lineStyle: { color: palette.grid } }
	};
}

/** Band P25–P75: der dritte Wert des Punktes ist P75 (der zweite die Höhe über P25) */
const bandFormat: ValueFormat = (height, point) => {
	const upper = point?.[2];
	return typeof upper === 'number' ? `${kw(upper - height)} bis ${kw(upper)}` : kw(height);
};

/** Geplanter SOC je Slot: er gilt am Ende des Slots, der Punkt liegt dort */
export function slotEndSocPoints(slots: PlanSlot[]): Point[] {
	return slots.map((slot): Point => [epoch(slot.start) + SLOT_MS, slot.soc_pct]);
}

/**
 * Preise und PV heute und morgen. Mit `battery` kommen der gemessene Akku-SOC (`[Zeit, %]`) und,
 * solange der Plan gilt, der geplante SOC ab jetzt dazu, beide auf einer unsichtbaren Achse 0–100 %.
 */
export function priceForecastOption(
	prices: PriceSlot[],
	forecast: ForecastSlot[],
	load: LoadForecast | null,
	nowIso: string,
	palette: Palette,
	battery?: { measured: [number, number][]; plan: Plan | null }
): EChartsCoreOption {
	const now = parseIso(nowIso);
	const loadSlots = load?.slots ?? [];
	const series: LineSeriesOption[] = [
		{
			id: 'import',
			name: 'Bezugspreis',
			type: 'line',
			step: 'end',
			yAxisIndex: 0,
			showSymbol: false,
			data: steps(prices, (slot) => slot.import_gross_ct),
			lineStyle: { color: palette.importPrice, width: 2 },
			itemStyle: { color: palette.importPrice },
			markLine: nowMarkLine(now, palette)
		}
	];
	if (prices.some((slot) => slot.feed_in_ct !== null)) {
		series.push({
			id: 'feed_in',
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
		// Band als gestapelte Fläche: unsichtbarer Sockel P25, darüber P75 − P25
		series.push(
			{
				id: 'pv_p25',
				name: 'PV P25',
				type: 'line',
				yAxisIndex: 1,
				stack: 'pv_band',
				smooth: true,
				showSymbol: false,
				silent: true,
				data: forecast.map((slot): Point => [epoch(slot.start), slot.p25_w / 1000]),
				lineStyle: { width: 0 },
				itemStyle: { color: palette.pv },
				areaStyle: { opacity: 0 }
			},
			{
				id: 'pv_band',
				name: 'PV P25–P75',
				type: 'line',
				yAxisIndex: 1,
				stack: 'pv_band',
				smooth: true,
				showSymbol: false,
				data: forecast.map((slot) => [
					epoch(slot.start),
					Math.max(0, slot.p75_w - slot.p25_w) / 1000,
					slot.p75_w / 1000
				]),
				lineStyle: { width: 0 },
				itemStyle: { color: palette.pv },
				areaStyle: { color: palette.pv, opacity: 0.2 }
			},
			{
				id: 'pv',
				name: 'PV-Prognose',
				type: 'line',
				yAxisIndex: 1,
				smooth: true,
				showSymbol: false,
				data: forecast.map((slot): Point => [epoch(slot.start), slot.p50_w / 1000]),
				lineStyle: { color: palette.pv },
				itemStyle: { color: palette.pv }
			}
		);
	}
	if (loadSlots.length > 0) {
		series.push({
			id: 'load',
			name: 'Lastprognose',
			type: 'line',
			yAxisIndex: 1,
			smooth: true,
			showSymbol: false,
			data: loadSlots.map((slot): Point => [epoch(slot.start), slot.p50_w / 1000]),
			lineStyle: { color: palette.load, type: 'dashed' },
			itemStyle: { color: palette.load }
		});
	}
	if (battery && battery.measured.length > 0) {
		series.push({
			id: 'soc',
			name: 'Akku-SOC',
			type: 'line',
			yAxisIndex: 2,
			showSymbol: false,
			data: battery.measured,
			lineStyle: { color: palette.text, width: 2 },
			itemStyle: { color: palette.text }
		});
	}
	const planSlots = battery?.plan?.valid
		? battery.plan.slots.filter((slot) => now === null || epoch(slot.start) + SLOT_MS > now)
		: [];
	if (planSlots.length > 0) {
		series.push({
			id: 'soc_plan',
			name: 'Akku-Plan',
			type: 'line',
			yAxisIndex: 2,
			showSymbol: false,
			data: slotEndSocPoints(planSlots),
			lineStyle: { color: palette.text, type: 'dashed' },
			itemStyle: { color: palette.text }
		});
	}
	const hasSoc = series.some((s) => s.yAxisIndex === 2);
	const base = baseOption(palette) as { legend: object; tooltip: object };
	return {
		...base,
		// der Sockel des Bandes gehört nicht in die Legende
		legend: {
			...base.legend,
			data: series.map((s) => s.name).filter((name) => name !== 'PV P25')
		},
		tooltip: {
			...base.tooltip,
			trigger: 'axis',
			formatter: axisTooltip({
				Bezugspreis: formatCt,
				Einspeisung: formatCt,
				'PV P25': null,
				'PV P25–P75': bandFormat,
				'PV-Prognose': kw,
				Lastprognose: kw,
				'Akku-SOC': formatSoc,
				'Akku-Plan': formatSoc
			})
		},
		yAxis: [
			valueAxis('ct/kWh', palette),
			valueAxis('kW', palette, forecast.length > 0 || loadSlots.length > 0),
			// SOC ohne eigene Beschriftung; die Achse bleibt unsichtbar, nur die Linien zählen
			...(hasSoc ? [{ ...valueAxis('%', palette, false), min: 0, max: 100 }] : [])
		],
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
	const series: LineSeriesOption[] = Object.entries(data.series).map(([id, entry], index) => {
		const unit = entry.unit === 'W' ? 'kW' : entry.unit; // Leistung in kW
		if (!units.includes(unit)) units.push(unit);
		formats[entry.label] = unitFormat(unit);
		const color = palette.series[index % palette.series.length];
		return {
			id,
			name: entry.label,
			type: 'line',
			yAxisIndex: units.indexOf(unit),
			showSymbol: false,
			data: withGaps(entry.points, data.step_s * 1000, entry.unit === 'W' ? 1000 : 1),
			lineStyle: { color, width: 1.5 },
			itemStyle: { color }
		};
	});
	const base = baseOption(palette) as { tooltip: object };
	return {
		...base,
		grid: { left: 8, right: 8, top: 40, bottom: 44, containLabel: true },
		tooltip: { ...base.tooltip, trigger: 'axis', formatter: axisTooltip(formats) },
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

/** Tooltip der Statistik: Beschriftung des Balkens und kWh je Reihe */
function statsTooltip(params: unknown): string {
	const items = (Array.isArray(params) ? params : [params]) as {
		marker?: string;
		name?: string;
		seriesName?: string;
		value?: unknown;
	}[];
	const lines = items.map((item) => {
		const text = typeof item.value === 'number' ? formatEnergy(item.value) : DASH;
		return `${item.marker ?? ''}${escapeHtml(item.seriesName ?? '')}: <b>${text}</b>`;
	});
	return [escapeHtml(items[0]?.name ?? ''), ...lines].join('<br>');
}

/**
 * Diagramm „Energie“ der Statistik: PV, Bezug und Einspeisung als Balken, der Verbrauch als Linie,
 * je Stunde (Tag), Tag (Woche, Monat) oder Monat (Jahr). Die Achse ist eine Rubrikenachse mit den
 * Beschriftungen aus `bucketLabels`, so zeigt ein Tag mit Zeitumstellung 23 bzw. 25 Balken.
 */
export function statsEnergyOption(stats: Stats, palette: Palette): EChartsCoreOption {
	const labels = bucketLabels(
		stats.period,
		stats.buckets.map((bucket) => bucket.start)
	);
	const colors = {
		pv: palette.pv,
		import: palette.series[0] ?? palette.muted,
		export: palette.feedIn,
		use: palette.load
	};
	const bars = (name: string, color: string, values: number[]): BarSeriesOption => ({
		name,
		type: 'bar',
		data: values,
		itemStyle: { color },
		barMaxWidth: 24
	});
	const series: (BarSeriesOption | LineSeriesOption)[] = [
		bars(
			'PV',
			colors.pv,
			stats.buckets.map((b) => b.pv_kwh)
		),
		bars(
			'Bezug',
			colors.import,
			stats.buckets.map((b) => b.import_kwh)
		),
		bars(
			'Einspeisung',
			colors.export,
			stats.buckets.map((b) => b.export_kwh)
		),
		{
			name: 'Verbrauch',
			type: 'line',
			data: stats.buckets.map((b) => b.consumption_kwh),
			showSymbol: false,
			lineStyle: { color: colors.use, width: 2 },
			itemStyle: { color: colors.use }
		}
	];
	const base = baseOption(palette) as { tooltip: object };
	return {
		...base,
		tooltip: { ...base.tooltip, trigger: 'axis', formatter: statsTooltip },
		xAxis: {
			type: 'category',
			data: labels,
			axisLabel: { color: palette.muted, hideOverlap: true },
			axisLine: { lineStyle: { color: palette.grid } },
			axisTick: { alignWithLabel: true }
		},
		yAxis: {
			...valueAxis('kWh', palette),
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series
	};
}

/**
 * Tooltip des Verlaufs je Auto: Beschriftung des Balkens und der Wert je Reihe in kWh bzw. €
 * (`null` als „–“); bei mehr als einer Reihe die Summe, die „–“ ist, sobald ein Wert fehlt.
 */
function vehicleTrendTooltip(format: (value: number | null) => string) {
	return (params: unknown): string => {
		const items = (Array.isArray(params) ? params : [params]) as {
			marker?: string;
			name?: string;
			seriesName?: string;
			value?: unknown;
		}[];
		const values = items.map((item) => (typeof item.value === 'number' ? item.value : null));
		const lines = items.map(
			(item, index) =>
				`${item.marker ?? ''}${escapeHtml(item.seriesName ?? '')}: <b>${format(values[index] ?? null)}</b>`
		);
		if (items.length > 1) {
			const known = values.every((value) => value !== null);
			const sum = known ? values.reduce<number>((acc, value) => acc + (value ?? 0), 0) : null;
			lines.push(`Summe: <b>${format(sum)}</b>`);
		}
		return [escapeHtml(items[0]?.name ?? ''), ...lines].join('<br>');
	};
}

/**
 * Diagramm „Laden je Auto“: je Tag (Woche, Monat) oder Monat (Jahr) ein gestapelter Balken. Ohne
 * Auswahl (`vehicle` ist `null`) eine Reihe je Auto aus `stats.vehicles`; mit Auswahl in kWh die
 * Reihen PV, Akku und Netz dieses Autos, in € eine Reihe „Kosten“. Lädt ein Auto in einem Balken
 * nicht, ist der Wert 0; nur ein unbekannter Preis bleibt `null` und damit eine Lücke.
 */
export function vehicleTrendOption(
	stats: Stats,
	vehicle: string | null,
	unit: 'kwh' | 'eur',
	colorOf: (key: string) => string,
	palette: Palette
): EChartsCoreOption {
	const labels = bucketLabels(
		stats.period,
		stats.vehicle_buckets.map((bucket) => bucket.start)
	);
	type Entry = Stats['vehicle_buckets'][number]['vehicles'][number];
	const bars = (
		name: string,
		color: string,
		value: (entry: Entry) => number | null,
		key: string
	): BarSeriesOption => ({
		name,
		type: 'bar',
		stack: 'autos',
		data: stats.vehicle_buckets.map((bucket) => {
			const entry = bucket.vehicles.find((item) => item.key === key);
			return entry === undefined ? 0 : value(entry);
		}),
		itemStyle: { color },
		barMaxWidth: 24
	});
	let series: BarSeriesOption[];
	if (vehicle === null) {
		series = stats.vehicles.map((item) =>
			bars(
				vehicleLabel(item.key, item.name),
				colorOf(item.key),
				(entry) => (unit === 'kwh' ? entry.kwh : entry.cost_eur),
				item.key
			)
		);
	} else if (unit === 'kwh') {
		series = [
			bars('PV', palette.pv, (entry) => entry.pv_kwh, vehicle),
			bars('Akku', palette.importPrice, (entry) => entry.battery_kwh, vehicle),
			bars('Netz', palette.series[0] ?? palette.muted, (entry) => entry.grid_kwh, vehicle)
		];
	} else {
		series = [bars('Kosten', colorOf(vehicle), (entry) => entry.cost_eur, vehicle)];
	}
	const base = baseOption(palette) as { tooltip: object };
	return {
		...base,
		tooltip: {
			...base.tooltip,
			trigger: 'axis',
			formatter: vehicleTrendTooltip(unit === 'kwh' ? formatEnergy : formatEur)
		},
		xAxis: {
			type: 'category',
			data: labels,
			axisLabel: { color: palette.muted, hideOverlap: true },
			axisLine: { lineStyle: { color: palette.grid } },
			axisTick: { alignWithLabel: true }
		},
		yAxis: {
			...valueAxis(unit === 'kwh' ? 'kWh' : '€', palette),
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series
	};
}

/** Tooltip einer Kategorieachse mit der Beschriftung des Tages und dem Wert je Reihe in W */
function dailyTooltip(params: unknown): string {
	const items = (Array.isArray(params) ? params : [params]) as {
		marker?: string;
		name?: string;
		seriesName?: string;
		value?: unknown;
	}[];
	const lines = items.map((item) => {
		const text = typeof item.value === 'number' ? `${formatNumber(item.value, 0)}${NBSP}W` : DASH;
		return `${item.marker ?? ''}${escapeHtml(item.seriesName ?? '')}: <b>${text}</b>`;
	});
	return [escapeHtml(items[0]?.name ?? ''), ...lines].join('<br>');
}

/** MAE je Tag von Profil und Modell als zwei Linien; Tage ohne Wert bleiben Lücken */
export function qualityDailyOption(
	series: { profile: DailyPoint[]; model: DailyPoint[] },
	palette: Palette
): EChartsCoreOption {
	const dates = [
		...new Set([...series.profile, ...series.model].map((point) => point.date))
	].sort();
	const line = (name: string, points: DailyPoint[], color: string): LineSeriesOption => {
		const byDate = new Map(points.map((point) => [point.date, point.mae_w]));
		return {
			name,
			type: 'line',
			data: dates.map((date) => byDate.get(date) ?? null),
			showSymbol: dates.length <= 31,
			lineStyle: { color, width: 2 },
			itemStyle: { color }
		};
	};
	const base = baseOption(palette) as { legend: object; tooltip: object };
	return {
		...base,
		tooltip: { ...base.tooltip, trigger: 'axis', formatter: dailyTooltip },
		legend: { ...base.legend, data: ['Profil', 'Modell'] },
		xAxis: {
			type: 'category',
			data: dates.map(formatDay),
			axisLabel: { color: palette.muted, hideOverlap: true },
			axisLine: { lineStyle: { color: palette.grid } },
			axisTick: { alignWithLabel: true }
		},
		yAxis: {
			...valueAxis('MAE (W)', palette),
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series: [
			line('Profil', series.profile, palette.series[0] ?? palette.muted),
			line('Modell', series.model, palette.series[2] ?? palette.load)
		]
	};
}

const percentFormat: ValueFormat = (value) => `${formatNumber(value, 0)}${NBSP}%`;

/**
 * Wahrscheinlichkeit angesteckt in den nächsten 48 Stunden: je Auto eine Stufenlinie in %, ein Punkt
 * je Viertelstunde und ein Schlusspunkt am Ende der letzten. Autos ohne Prognose bleiben weg.
 */
export function presenceOption(
	cars: { name: string; presence: { start: string; p: number }[] }[],
	palette: Palette
): EChartsCoreOption {
	const shown = cars.filter((car) => car.presence.length > 0);
	const series: LineSeriesOption[] = shown.map((car, index) => {
		const color = palette.series[index % palette.series.length] ?? palette.load;
		const data: Point[] = car.presence.map((slot) => [epoch(slot.start), slot.p * 100]);
		const last = car.presence.at(-1);
		if (last) data.push([epoch(last.start) + SLOT_MS, last.p * 100]);
		return {
			name: car.name,
			type: 'line',
			step: 'start',
			showSymbol: false,
			data,
			lineStyle: { color, width: 2 },
			itemStyle: { color }
		};
	});
	const base = baseOption(palette) as { tooltip: object };
	return {
		...base,
		tooltip: {
			...base.tooltip,
			trigger: 'axis',
			formatter: axisTooltip(Object.fromEntries(shown.map((car) => [car.name, percentFormat])))
		},
		yAxis: {
			...valueAxis('%', palette),
			min: 0,
			max: 100,
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series
	};
}
