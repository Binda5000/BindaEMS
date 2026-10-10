// Tagesbilanz aus der Abrechnung: formatierte Zellen je Tag und die Zählerstände für VRM
import type { DaySummary } from '$lib/api/schemas';
import {
	DASH,
	NBSP,
	formatDay,
	formatEnergy,
	formatEur,
	formatNumber,
	formatRatio
} from '$lib/format';

export type BalanceColumn =
	| 'coverage'
	| 'pv'
	| 'import'
	| 'export'
	| 'charge'
	| 'discharge'
	| 'house'
	| 'wallboxes'
	| 'cost'
	| 'revenue'
	| 'net'
	| 'autarky'
	| 'selfConsumption';

export const BALANCE_COLUMNS: { key: BalanceColumn; label: string }[] = [
	{ key: 'coverage', label: 'Abdeckung' },
	{ key: 'pv', label: 'PV' },
	{ key: 'import', label: 'Bezug' },
	{ key: 'export', label: 'Einspeisung' },
	{ key: 'charge', label: 'Akku geladen' },
	{ key: 'discharge', label: 'Akku entladen' },
	{ key: 'house', label: 'Haus' },
	{ key: 'wallboxes', label: 'Wallboxen' },
	{ key: 'cost', label: 'Kosten' },
	{ key: 'revenue', label: 'Erlös' },
	{ key: 'net', label: 'Netto' },
	{ key: 'autarky', label: 'Autarkie' },
	{ key: 'selfConsumption', label: 'Eigenverbrauch' }
];

export interface BalanceRow {
	date: string;
	day: string;
	cells: Record<BalanceColumn, string>;
	counters: { name: string; value: string }[];
}

const COUNTERS: [RegExp, (match: RegExpMatchArray) => string][] = [
	[/^grid\.energy_import_kwh$/, () => 'Netz Bezug'],
	[/^grid\.energy_export_kwh$/, () => 'Netz Einspeisung'],
	[/^pv\.([^.]+)\.energy_kwh$/, (m) => `PV ${m[1]}`],
	[/^load\.([^.]+)\.energy_kwh$/, (m) => `Last ${m[1]}`],
	[/^wallbox\.([^.]+)\.total_kwh$/, (m) => `Wallbox ${m[1]}`]
];

export function counterLabel(key: string): string {
	for (const [pattern, label] of COUNTERS) {
		const match = key.match(pattern);
		if (match) return label(match);
	}
	return key;
}

/**
 * Abdeckung: Slots mit Daten und Anteil der aufgezeichneten Zeit. Der Anteil wird abgerundet –
 * „100 %“ heißt lückenlos (der core zieht jede Lücke über 5 s ab), auch wenn alle Slots da sind.
 */
function coverageText(day: DaySummary): string {
	const percent = day.coverage >= 1 ? '100' : formatNumber(Math.floor(day.coverage * 1000) / 10, 1);
	return `${day.slots} von ${day.expected_slots} · ${percent}${NBSP}%`;
}

export function balanceRows(days: DaySummary[]): BalanceRow[] {
	return days.map((day) => {
		const row: BalanceRow = {
			date: day.date,
			day: formatDay(day.date),
			cells: {
				coverage: coverageText(day),
				pv: formatEnergy(day.pv_kwh),
				import: formatEnergy(day.import_kwh),
				export: formatEnergy(day.export_kwh),
				charge: formatEnergy(day.battery_charge_kwh),
				discharge: formatEnergy(day.battery_discharge_kwh),
				house: formatEnergy(day.house_kwh),
				wallboxes: formatEnergy(Object.values(day.wallbox_kwh).reduce((sum, kwh) => sum + kwh, 0)),
				cost: formatEur(day.cost_eur),
				revenue: formatEur(day.revenue_eur),
				net: formatEur(day.net_cost_eur),
				autarky: formatRatio(day.autarky),
				selfConsumption: formatRatio(day.self_consumption)
			},
			counters: Object.entries(day.counter_kwh).map(([key, kwh]) => ({
				name: counterLabel(key),
				value: formatEnergy(kwh)
			}))
		};
		// ohne einen Slot liefert die Abrechnung Nullen: das sind fehlende Werte, kein Verbrauch
		if (day.slots === 0) {
			for (const { key } of BALANCE_COLUMNS) if (key !== 'coverage') row.cells[key] = DASH;
			row.counters = [];
		}
		return row;
	});
}
