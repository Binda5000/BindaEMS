// Aufbereitung der Statistik für die Anzeige: Kacheln, Zeilen der Kostenkarte, Hinweise
import type { Stats } from '$lib/api/schemas';
import { formatDay, formatEur, formatRatio, formatTime } from '$lib/format';
import type { Period } from './period';

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

const MONTH_SHORT = [
	'Jän.',
	'Feb.',
	'März',
	'Apr.',
	'Mai',
	'Juni',
	'Juli',
	'Aug.',
	'Sep.',
	'Okt.',
	'Nov.',
	'Dez.'
];

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
			return MONTH_SHORT[Number(start.slice(5, 7)) - 1] ?? start;
	}
}
