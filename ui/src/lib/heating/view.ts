// Aufbereitung der Heizung für die Anzeige: Zeilen der Karten, Diagramm der Wärmebedarfsgeraden, CSV
import type { EChartsCoreOption } from 'echarts/core';
import type { HeatDemand, HeatingStats } from '$lib/api/schemas';
import { baseOption, valueAxis } from '$lib/charts/options';
import type { Palette } from '$lib/charts/palette';
import { DASH, NBSP, formatDay, formatEur, formatNumber, formatNumberMax } from '$lib/format';
import { MONTHS } from '$lib/stats/period';

type Maybe = number | null;

export interface HeatingRow {
	id: string;
	label: string;
	value: string;
	/** kleiner Zusatz unter dem Wert */
	note?: string;
}

const unit = (value: Maybe, digits: number, name: string) =>
	value === null ? DASH : `${formatNumber(value, digits)}${NBSP}${name}`;

export const kwhText = (value: Maybe) => unit(value, 1, 'kWh');

/** `2026-10-05` als „05.10.2026“ */
export function dateText(iso: string): string {
	const [year, month, day] = iso.split('-');
	return `${day}.${month}.${year}`;
}

/** Karte „Heizung“: ohne Wert „–“ (nie 0) */
export function heatingRows(heating: HeatingStats): HeatingRow[] {
	const oil =
		heating.oil_l === null
			? DASH
			: `${unit(heating.oil_l, 1, 'l')}${heating.oil_eur === null ? '' : ` · ${formatEur(heating.oil_eur)}`}`;
	return [
		{ id: 'burner', label: 'Brenner lief', value: unit(heating.burner_h, 1, 'h') },
		{ id: 'oil_heat', label: 'Wärme aus Öl', value: kwhText(heating.oil_heat_kwh) },
		{ id: 'oil', label: 'Öl', value: oil },
		{
			id: 'ac',
			label: 'Klimaanlage heizt',
			value:
				heating.ac_heat_el_kwh === null ? DASH : `${unit(heating.ac_heat_el_kwh, 1, 'kWh')} Strom`,
			note:
				heating.ac_heat_el_kwh === null || heating.ac_heat_kwh === null
					? undefined
					: `≈ ${kwhText(heating.ac_heat_kwh)} Wärme`
		},
		{ id: 'demand', label: 'Wärmebedarf', value: kwhText(heating.demand_kwh) },
		{
			id: 'hdd',
			label: 'Heizgradtage',
			value: unit(heating.hdd_kd, 1, 'K·d'),
			note: 'Heizgrenze 15\u00a0°C'
		},
		{
			id: 'days',
			label: 'Vollständige Tage',
			value: `${heating.complete_days} von ${heating.days}`
		}
	];
}

export const SETTINGS_MISSING = 'Nennleistung fehlt: unter Einstellungen → Ölheizung eintragen.';

/** Hinweise unter der Karte; `first` ist der erste Tag des Zeitraums */
export function heatingHints(heating: HeatingStats, first: string): string[] {
	const hints: string[] = [];
	if (heating.settings_missing) hints.push(SETTINGS_MISSING);
	if (heating.first_day !== null && heating.first_day > first) {
		hints.push(`Brennerstatus ab ${dateText(heating.first_day)}.`);
	}
	return hints;
}

/** Zeilen über der Geraden: Grundbedarf, Steigung, Güte; ohne Ergebnis der Grund der API */
export function fitLines(demand: HeatDemand): string[] {
	const fit = demand.fit;
	if (fit === null) return [demand.reason ?? 'Noch kein Ergebnis.'];
	const lines = [
		`Grundbedarf ${unit(fit.base_kwh_day, 1, 'kWh')} je Tag (vor allem Warmwasser)`,
		`Dazu ${unit(fit.per_kd, 1, 'kWh')} je Heizgradtag`,
		`R² ${formatNumber(fit.r2, 2)} aus ${fit.n} Tagen`
	];
	if (fit.through_origin) lines.push('Ohne Grundbedarf gerechnet (er wäre negativ).');
	return lines;
}

/** „Bisher in dieser Saison: … kWh Wärme, … l Öl (… €), … Heizgradtage“ */
export function seasonTotalsText(demand: HeatDemand): string {
	const { totals } = demand;
	const eur = totals.oil_eur === null ? '' : ` (${formatEur(totals.oil_eur)})`;
	const hdd = totals.hdd_kd === null ? DASH : formatNumber(totals.hdd_kd, 1);
	return (
		`Bisher in dieser Saison: ${kwhText(totals.demand_kwh)} Wärme, ` +
		`${unit(totals.oil_l, 1, 'l')} Öl${eur}, ${hdd} Heizgradtage`
	);
}

/** Strom der Klimaanlage beim Heizen je Monat, älteste zuerst */
export function acMonthRows(demand: HeatDemand): { month: string; label: string; value: string }[] {
	return Object.entries(demand.ac_by_month)
		.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
		.map(([month, kwh]) => {
			const [year, number] = month.split('-');
			const name = MONTHS[Number(number) - 1]?.short ?? number;
			return { month, label: `${name} ${year}`, value: kwhText(kwh) };
		});
}

export function acNote(demand: HeatDemand): string {
	return (
		'Die Wärme aus Öl ist Brennerlaufzeit × Nennleistung; Bereitschaftsverluste des Kessels sind ' +
		`darin enthalten. Die Wärme der Klimaanlage ist Strom × ${formatNumberMax(demand.ac_cop, 1)} (Schätzung).`
	);
}

interface Dot {
	value: [number, number];
	day: string;
}

/**
 * Wärmebedarf je Tag über den Heizgradtagen: Punkte der Tage in der Auswertung („verwendet“) und
 * der übrigen („nicht verwendet“), dazu die Gerade der Anpassung von 0 bis zum größten K·d.
 */
export function heatDemandOption(demand: HeatDemand, palette: Palette): EChartsCoreOption {
	const dots = (used: boolean): Dot[] =>
		demand.points.flatMap((point) =>
			point.used === used && point.hdd_kd !== null && point.demand_kwh !== null
				? [{ value: [point.hdd_kd, point.demand_kwh] as [number, number], day: point.day }]
				: []
		);
	const maxKd = Math.max(0, ...demand.points.map((point) => point.hdd_kd ?? 0));
	const fit = demand.fit;
	const accent = palette.series[2] ?? palette.load;
	const other = palette.muted;
	const scatter = (name: string, used: boolean, color: string) => ({
		name,
		type: 'scatter' as const,
		data: dots(used),
		symbolSize: 8,
		itemStyle: { color, opacity: used ? 0.9 : 0.6 }
	});
	const series = [
		scatter('verwendet', true, accent),
		scatter('nicht verwendet', false, other),
		...(fit === null
			? []
			: [
					{
						name: 'Gerade',
						type: 'line' as const,
						data: [
							[0, fit.base_kwh_day],
							[maxKd, fit.base_kwh_day + fit.per_kd * maxKd]
						],
						showSymbol: false,
						lineStyle: { color: palette.pv, width: 2 },
						itemStyle: { color: palette.pv },
						silent: true
					}
				])
	];
	const base = baseOption(palette) as { tooltip: object; grid: object };
	return {
		...base,
		grid: { ...base.grid, bottom: 28 },
		legend: { show: false, data: series.map((item) => item.name) },
		tooltip: {
			...base.tooltip,
			trigger: 'item',
			formatter: (params: unknown) => {
				const item = params as { seriesType?: string; data?: Dot };
				if (item.seriesType !== 'scatter' || !item.data) return '';
				const [kd, kwh] = item.data.value;
				return `${formatDay(item.data.day)}<br>${unit(kd, 1, 'K·d')}: <b>${unit(kwh, 1, 'kWh')}</b>`;
			}
		},
		xAxis: {
			...valueAxis('Heizgradtage (K·d)', palette),
			nameLocation: 'middle',
			nameGap: 28,
			min: 0,
			axisLine: { lineStyle: { color: palette.grid } },
			splitLine: { show: false }
		},
		yAxis: {
			...valueAxis('kWh je Tag', palette),
			min: 0,
			splitLine: { show: true, lineStyle: { color: palette.grid } }
		},
		series
	};
}

/** Name der Datei: `waermebedarf-2026-27.csv` */
export function heatingFilename(demand: HeatDemand): string {
	return `waermebedarf-${demand.season.replace('/', '-')}.csv`;
}

const CSV_HEADER = [
	'Tag',
	'Brennerstunden',
	'Wärme Öl kWh',
	'Öl l',
	'Klima Strom heizen kWh',
	'Klima Wärme kWh (geschätzt)',
	'Wärmebedarf kWh',
	'Heizgradtage K·d',
	'Außentemperatur °C',
	'vollständig',
	'in Auswertung',
	'Hinweis'
];

/** Zahl mit Komma, ohne Tausenderpunkt und ohne „-0“; unbekannt bleibt leer (nie 0) */
function csvNumber(value: Maybe, digits: number): string {
	if (value === null) return '';
	const text = value.toFixed(digits);
	return (/^-0(\.0*)?$/.test(text) ? text.slice(1) : text).replace('.', ',');
}

function csvField(text: string): string {
	return /[;"\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/**
 * Die Tage der Heizsaison als CSV für Excel (de-AT) wie die der Ladesessions: Semikolon, `\r\n`, BOM.
 */
export function heatingCsv(demand: HeatDemand): string {
	const yesNo = (value: boolean) => (value ? 'ja' : 'nein');
	const lines = demand.points.map((point) =>
		[
			dateText(point.day),
			csvNumber(point.burner_h, 2),
			csvNumber(point.oil_heat_kwh, 2),
			csvNumber(point.oil_l, 2),
			csvNumber(point.ac_heat_el_kwh, 2),
			csvNumber(point.ac_heat_kwh, 2),
			csvNumber(point.demand_kwh, 2),
			csvNumber(point.hdd_kd, 2),
			csvNumber(point.outdoor_mean_c, 1),
			yesNo(point.complete),
			yesNo(point.used),
			point.note ?? ''
		]
			.map(csvField)
			.join(';')
	);
	return `\ufeff${[CSV_HEADER.join(';'), ...lines].join('\r\n')}`;
}
