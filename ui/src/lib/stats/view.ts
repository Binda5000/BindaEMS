// Aufbereitung der Statistik für die Anzeige: Kacheln, Hinweise und die Zeilen der Karten
import type { ChargingSession, Stats } from '$lib/api/schemas';
import {
	DASH,
	NBSP,
	formatDateTime,
	formatDay,
	formatEnergy,
	formatEur,
	formatNumber,
	formatPower,
	formatRatio,
	formatTime
} from '$lib/format';
import { VIENNA, offsetMinutes, parseIso, todayVienna } from '$lib/time';
import { MONTHS, periodTitle, type Period } from './period';

export interface Tile {
	id: 'autarky' | 'self_consumption' | 'cost' | 'savings' | 'revenue';
	label: string;
	/** fertig formatiert, „–“ wenn unbekannt */
	value: string;
	subtitle?: string;
}

export interface Row {
	id: string;
	label: string;
	value: string;
	/** hervorgehoben (Summe) */
	strong?: boolean;
}

/** Autarkie, Eigenverbrauch, Kosten (mit Grundgebühren und Leistungspreis), Ersparnis, Einspeiseerlös */
export function tiles(stats: Stats): Tile[] {
	return [
		{ id: 'autarky', label: 'Autarkie', value: formatRatio(stats.ratios.autarky) },
		{
			id: 'self_consumption',
			label: 'Eigenverbrauch',
			value: formatRatio(stats.ratios.self_consumption)
		},
		{ id: 'cost', label: 'Kosten', value: formatEur(stats.money.total_eur) },
		{
			id: 'savings',
			label: 'Ersparnis',
			value: formatEur(stats.money.savings_no_plant_eur),
			subtitle: 'gegenüber Fixtarif ohne Anlage'
		},
		{ id: 'revenue', label: 'Einspeiseerlös', value: formatEur(stats.money.revenue_eur) }
	];
}

/**
 * Zeilen der Karte „Kosten“. Der Leistungspreis steht nur in Monat und Jahr und nur, wenn im
 * Zeitraum einer gilt (`power_eur` ist sonst `null`).
 */
export function costRows(stats: Stats): Row[] {
	const { money } = stats;
	const monthly = stats.period === 'month' || stats.period === 'year';
	const rows: Row[] = [
		{ id: 'import', label: 'Bezug', value: formatEur(money.import_eur) },
		{ id: 'revenue', label: 'Einspeiseerlös', value: formatEur(money.revenue_eur) },
		{ id: 'net', label: 'Netto', value: formatEur(money.net_eur) },
		{ id: 'fees', label: 'Grundgebühren', value: formatEur(money.fees_eur) }
	];
	if (monthly && money.power_eur !== null) {
		rows.push({ id: 'power', label: 'Leistungspreis', value: formatEur(money.power_eur) });
	}
	rows.push({ id: 'total', label: 'Summe', value: formatEur(money.total_eur), strong: true });
	return rows;
}

/** Die beiden Ersparnisse unter der Kostenkarte (ohne Grundgebühren und Leistungspreis) */
export function savingsRows(stats: Stats): Row[] {
	return [
		{
			id: 'no_plant',
			label: 'Ersparnis ohne Anlage',
			value: formatEur(stats.money.savings_no_plant_eur)
		},
		{
			id: 'same_import',
			label: 'Ersparnis bei gleichem Netzbezug',
			value: formatEur(stats.money.savings_same_import_eur)
		}
	];
}

/** Abdeckung, Schätzung und „bis jetzt“ für den laufenden Zeitraum */
export function hints(stats: Stats, running: boolean): string[] {
	const result = [`Abdeckung ${formatRatio(stats.coverage)}`];
	if (stats.estimated_slots > 0) {
		const n = stats.estimated_slots;
		result.push(`${n} ${n === 1 ? 'Viertelstunde' : 'Viertelstunden'} geschätzt`);
	}
	if (running) result.push('bis jetzt');
	return result;
}

/** Beschriftung eines Balkens: Stunde (Tag), Tag (Woche, Monat), Monat (Jahr) */
export function bucketLabel(period: Period, start: string): string {
	switch (period) {
		case 'day':
			return formatTime(start);
		case 'week':
			return formatDay(start);
		case 'month': {
			const [, month, day] = start.split('-');
			return `${day}.${month}.`;
		}
		case 'year':
			return MONTHS[Number(start.slice(5, 7)) - 1]?.short ?? start;
	}
}

/**
 * Beschriftungen aller Balken. Am Tag der Umstellung im Herbst gibt es die Stunde 02:00 zweimal;
 * sie bekommt dann die Zone dazu („02:00 MESZ“, „02:00 MEZ“).
 */
export function bucketLabels(period: Period, starts: string[]): string[] {
	const labels = starts.map((start) => bucketLabel(period, start));
	if (period !== 'day') return labels;
	const count = new Map<string, number>();
	for (const label of labels) count.set(label, (count.get(label) ?? 0) + 1);
	return labels.map((label, index) => {
		if ((count.get(label) ?? 0) < 2) return label;
		const ms = parseIso(starts[index] ?? '');
		if (ms === null) return label;
		return `${label} ${offsetMinutes(ms) >= 120 ? 'MESZ' : 'MEZ'}`;
	});
}

/** Anzeigename eines Autos: Gast und Unbekannt deutsch, ein Auto ohne Namen mit seinem Schlüssel */
export function vehicleLabel(key: string, name: string): string {
	if (key === 'guest') return 'Gast';
	if (key === 'unknown') return 'Unbekannt';
	return name.trim() || key;
}

export interface VehicleRow {
	key: string;
	name: string;
	energy: string;
	pv: string;
	battery: string;
	grid: string;
	cost: string;
	/** ohne Kosten oder ohne Energie „–“ */
	ctPerKwh: string;
	sessions: string;
}

/** Karte „Autos“: je Auto eine Zeile, dazu die Energie vom Akku in die Autos (ohne Autos `null`) */
export function vehicleRows(stats: Stats): {
	rows: VehicleRow[];
	batteryToVehicles: string | null;
} {
	const rows = stats.vehicles.map((vehicle) => ({
		key: vehicle.key,
		name: vehicleLabel(vehicle.key, vehicle.name),
		energy: formatEnergy(vehicle.kwh),
		pv: formatEnergy(vehicle.pv_kwh),
		battery: formatEnergy(vehicle.battery_kwh),
		grid: formatEnergy(vehicle.grid_kwh),
		cost: formatEur(vehicle.cost_eur),
		ctPerKwh:
			vehicle.cost_eur === null || !(vehicle.kwh > 0)
				? DASH
				: formatNumber((vehicle.cost_eur / vehicle.kwh) * 100, 2),
		sessions: String(vehicle.sessions)
	}));
	return {
		rows,
		batteryToVehicles: rows.length > 0 ? formatEnergy(stats.battery_to_vehicles_kwh) : null
	};
}

export interface SessionRow {
	id: number;
	start: string;
	/** „läuft“, solange die Session offen ist; am selben Tag nur die Uhrzeit */
	end: string;
	wallbox: string;
	vehicle: string;
	energy: string;
	/** „PV / Akku / Netz“ in kWh */
	split: string;
	/** Rohwerte der Aufteilung (kWh) für den Balken */
	pv_kwh: number;
	battery_kwh: number;
	grid_kwh: number;
	cost: string;
}

function sessionEnd(session: ChargingSession): string {
	if (session.open || session.end === null) return 'läuft';
	const start = parseIso(session.start);
	const end = parseIso(session.end);
	if (
		start !== null &&
		end !== null &&
		todayVienna(new Date(start)) === todayVienna(new Date(end))
	) {
		return formatTime(session.end);
	}
	return formatDateTime(session.end);
}

/** Karte „Ladesessions“; die Reihenfolge (neueste zuerst) kommt vom Server */
export function sessionRows(sessions: ChargingSession[]): SessionRow[] {
	return sessions.map((session) => ({
		id: session.id,
		start: formatDateTime(session.start),
		end: sessionEnd(session),
		wallbox: session.wallbox_name,
		vehicle: vehicleLabel(session.vehicle, session.vehicle_name),
		energy: formatEnergy(session.kwh),
		split: `${[session.pv_kwh, session.battery_kwh, session.grid_kwh]
			.map((kwh) => formatNumber(kwh, 1))
			.join(' / ')}${NBSP}kWh`,
		pv_kwh: session.pv_kwh,
		battery_kwh: session.battery_kwh,
		grid_kwh: session.grid_kwh,
		cost: formatEur(session.cost_eur)
	}));
}

/** So viele Sessions zeigt die Karte „Ladesessions“ zuerst und je „Weitere 50 anzeigen“ mehr */
export const SESSIONS_PAGE = 50;

const CSV_HEADER = [
	'Beginn',
	'Ende',
	'Wallbox',
	'Auto',
	'Energie kWh',
	'PV kWh',
	'Akku kWh',
	'Netz kWh',
	'Kosten EUR'
];

// „09.10.2026 08:00“ in Wiener Zeit, mit Leerzeichen statt Komma wie `formatDateTime`
const CSV_TIME = new Intl.DateTimeFormat('de-AT', {
	timeZone: VIENNA,
	day: '2-digit',
	month: '2-digit',
	year: 'numeric',
	hour: '2-digit',
	minute: '2-digit',
	hourCycle: 'h23'
});

function csvTime(iso: string | null): string {
	const ms = iso === null ? null : parseIso(iso);
	if (ms === null) return '';
	const part = Object.fromEntries(CSV_TIME.formatToParts(ms).map((p) => [p.type, p.value]));
	return `${part.day}.${part.month}.${part.year} ${part.hour}:${part.minute}`;
}

/** Zahl mit Komma, ohne Tausenderpunkt (Excel liest sie dann als Zahl) und ohne „-0“ */
function csvNumber(value: number, digits: number): string {
	const text = value.toFixed(digits);
	return (/^-0(\.0*)?$/.test(text) ? text.slice(1) : text).replace('.', ',');
}

function csvField(text: string): string {
	return /[;"\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/**
 * Die Sessions als CSV für Excel (de-AT): Semikolon, Zeilenende `\r\n`, mit BOM am Anfang. Kopfzeile
 * und eine Zeile je Session in der Reihenfolge der Liste; Zeiten in Wiener Zeit, eine laufende Session
 * hat kein Ende, unbekannte Kosten bleiben leer (nie 0).
 */
export function sessionsCsv(sessions: ChargingSession[]): string {
	const lines = sessions.map((session) =>
		[
			csvTime(session.start),
			session.open ? '' : csvTime(session.end),
			session.wallbox_name,
			vehicleLabel(session.vehicle, session.vehicle_name),
			csvNumber(session.kwh, 3),
			csvNumber(session.pv_kwh, 3),
			csvNumber(session.battery_kwh, 3),
			csvNumber(session.grid_kwh, 3),
			session.cost_eur === null ? '' : csvNumber(session.cost_eur, 2)
		]
			.map(csvField)
			.join(';')
	);
	return `\ufeff${[CSV_HEADER.join(';'), ...lines].join('\r\n')}`;
}

export interface ConsumerRow {
	id: number;
	name: string;
	value: string;
	/** der Grund, wenn die Energie fehlt, sonst `null` */
	note: string | null;
}

/** Karte „Verbraucher“: ohne Wert „–“ und darunter der Grund */
export function consumerRows(stats: Stats): ConsumerRow[] {
	return stats.consumers.map((consumer) => ({
		id: consumer.id,
		name: consumer.name,
		value: formatEnergy(consumer.kwh),
		note: consumer.kwh === null ? consumer.note : null
	}));
}

export interface PeakRow {
	month: string;
	peak: string;
	at: string;
	price: string;
}

/** Karte „Monatsspitzen“ */
export function peakRows(stats: Stats): PeakRow[] {
	return stats.month_peaks.map((peak) => ({
		month: periodTitle('month', `${peak.month}-01`),
		peak: formatPower(peak.kw * 1000),
		at: formatDateTime(peak.slot_start),
		price: formatEur(peak.power_eur)
	}));
}

export interface CheckRow {
	signal: string;
	label: string;
	ledger: string;
	counter: string;
	deviation: string;
}

function checkLabel(signal: string, wallboxNames: Record<string, string>): string {
	switch (signal) {
		case 'import':
			return 'Netzbezug';
		case 'export':
			return 'Einspeisung';
		case 'pv':
			return 'PV (ohne Markierung)';
	}
	if (signal.startsWith('wb:')) {
		const key = signal.slice(3);
		return `Wallbox ${wallboxNames[key] ?? key}`;
	}
	return signal;
}

/**
 * Die Summen mit mindestens einem Tag mit Zählerwert. Eine Größe ohne Zählertag liefert der Server
 * mit `counter_kwh: null` und `ledger_kwh: 0` (die Abrechnung zählt nur an Tagen mit Zählerwert);
 * sie hat keine Summe und fehlt hier.
 */
function countedTotals(stats: Stats): Stats['checks']['totals'] {
	return stats.checks.totals.filter((total) => total.counter_kwh !== null);
}

/**
 * Karte „Abgleich mit den Zählern“: eine Zeile je Größe mit Zählerwert, `wallboxNames` nach
 * Schlüssel
 */
export function checkRows(stats: Stats, wallboxNames: Record<string, string>): CheckRow[] {
	return countedTotals(stats).map((total) => ({
		signal: total.signal,
		label: checkLabel(total.signal, wallboxNames),
		ledger: formatEnergy(total.ledger_kwh),
		counter: formatEnergy(total.counter_kwh),
		deviation:
			total.deviation_pct === null ? DASH : `${formatNumber(total.deviation_pct, 1)}${NBSP}%`
	}));
}

/** „a“, „a und b“, „a, b und c“ */
function joinList(items: string[]): string {
	return items.length === 1 ? items[0] : `${items.slice(0, -1).join(', ')} und ${items.at(-1)}`;
}

/**
 * „Ohne Zählerwerte: Wallbox EVCS.“: die Größen, die `checkRows` mangels Zählertag weglässt, mit
 * ihren Namen aus der Tabelle; `null`, wenn keine fehlt oder keine Zeile bleibt (dann sagt
 * `checkNotice`, dass Zählerwerte fehlen).
 */
export function missingCounters(stats: Stats, wallboxNames: Record<string, string>): string | null {
	if (countedTotals(stats).length === 0) return null;
	const missing = stats.checks.totals
		.filter((total) => total.counter_kwh === null)
		.map((total) => checkLabel(total.signal, wallboxNames));
	return missing.length === 0 ? null : `Ohne Zählerwerte: ${joinList(missing)}.`;
}

/**
 * „Mehr als 1 % Abweichung am 12.10. und 15.10.“; ohne geprüfte Tage der Hinweis darauf, mit
 * geprüften Tagen ohne Summen und Markierungen, dass Zählerwerte fehlen.
 */
export function checkNotice(stats: Stats): string | null {
	if (stats.checks.days === 0) return 'Noch kein Tag abgeglichen.';
	const days = stats.checks.flagged_days.map((day) => `${day.slice(8, 10)}.${day.slice(5, 7)}.`);
	if (days.length === 0) {
		return countedTotals(stats).length === 0 ? 'Keine Zählerwerte für diesen Zeitraum.' : null;
	}
	return `Mehr als 1 % Abweichung am ${joinList(days)}`;
}

/** Die Sätze der Karte „Geplantes Laden“; `null`, wenn es im Zeitraum keine geplante Ladung gab */
export interface PlannedLines {
	/** „{a} von {b} machbaren Ladungen erreicht ({pct} %)“ (Abnahme „Ziel-SOC“) */
	rate: string;
	/** was nicht in die Quote zählt */
	outside: string;
	changed: string;
}

export function plannedLines(planned: Stats['planned']): PlannedLines | null {
	const { reached, feasible, infeasible, early, unknown, changed } = planned;
	if (feasible + infeasible + early + unknown + changed === 0) return null;
	// abgerundet: 94,6 % sind keine 95 %, die die Abnahme verlangt
	const pct = feasible > 0 ? formatNumber(Math.floor((reached * 100) / feasible), 0) : DASH;
	const n = (value: number) => formatNumber(value, 0);
	return {
		rate: `${n(reached)} von ${n(feasible)} machbaren Ladungen erreicht (${pct}${NBSP}%)`,
		outside: `Nicht machbar: ${n(infeasible)} · Vor der Abfahrt abgesteckt: ${n(early)} · Ohne Ladestand: ${n(unknown)}`,
		changed: `Vor der Abfahrt geändert oder verlassen: ${n(changed)}`
	};
}
