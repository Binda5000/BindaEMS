// Tagesbilanz aus der Abrechnung: formatierte Zellen je Tag und die Zählerstände für VRM
import type { DaySummary } from '$lib/api/schemas';
import { formatDay, formatEnergy, formatEur, formatRatio } from '$lib/format';

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

export function balanceRows(days: DaySummary[]): BalanceRow[] {
	return days.map((day) => ({
		date: day.date,
		day: formatDay(day.date),
		cells: {
			coverage: `${day.slots} von ${day.expected_slots}`,
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
	}));
}
