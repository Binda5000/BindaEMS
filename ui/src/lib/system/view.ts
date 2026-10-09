// Texte und Zeilen der System-Seite
import type { CoreState, PriceStatus, Reading } from '$lib/api/schemas';
import { DASH, formatNumber } from '$lib/format';
import { parseIso } from '$lib/time';

const COMPONENTS: Record<string, string> = {
	core: 'core',
	prices: 'Preise',
	forecast: 'PV-Prognose',
	ledger: 'Abrechnung',
	influx: 'InfluxDB',
	ha: 'Home Assistant'
};

const SELFCHECKS: Record<string, string> = {
	phases: 'Phasen (vebus)',
	ess_mode: 'ESS-Modus',
	batterylife: 'BatteryLife',
	dess: 'Dynamic ESS',
	schedules: 'Victron-Ladefenster',
	min_soc: 'Min-SOC',
	fresh_data: 'Datenfrische'
};

const SIGNAL_NUMBER = new Intl.NumberFormat('de-AT', { maximumFractionDigits: 3 });

export function componentLabel(name: string): string {
	return COMPONENTS[name] ?? name;
}

export function selfcheckLabel(id: string): string {
	if (id.startsWith('evcs_mode.')) return `EVCS-Modus ${id.slice('evcs_mode.'.length)}`;
	return SELFCHECKS[id] ?? id;
}

export function signalValue(value: Reading['v']): string {
	if (value === null) return DASH;
	if (typeof value === 'boolean') return value ? 'ja' : 'nein';
	if (typeof value === 'number') return SIGNAL_NUMBER.format(value);
	return value;
}

export interface SignalRow {
	name: string;
	value: string;
	quality: Reading['q'];
	ageS: number | null;
}

export function signalRows(
	state: CoreState | null | undefined,
	nowMs: number,
	filter: string
): SignalRow[] {
	const needle = filter.trim().toLowerCase();
	return Object.entries(state?.signals ?? {})
		.filter(([name]) => name.toLowerCase().includes(needle))
		.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
		.map(([name, reading]) => {
			const ts = parseIso(reading.ts);
			return {
				name,
				value: signalValue(reading.v),
				quality: reading.q,
				ageS: ts === null ? null : Math.max(0, Math.round((nowMs - ts) / 1000))
			};
		});
}

const VAT_WORDS = { net: 'netto', gross: 'brutto' } as const;

/** Ob die Preise netto oder brutto kommen und wie das bestimmt wurde */
export function vatText(status: PriceStatus): string {
	if (status.vat_mode === null) return 'noch nicht bestimmt';
	const word = VAT_WORDS[status.vat_mode];
	const detection = status.vat_detection;
	if (detection === null || detection.ratio === null) return word;
	const ratio = `Verhältnis ${formatNumber(detection.ratio, 3)} aus ${detection.slots} Slots`;
	if (detection.result === status.vat_mode) return `${word} (erkannt, ${ratio})`;
	if (detection.result === null) return `${word} (${ratio}, nicht eindeutig)`;
	return word;
}

/** „vor 40 s“, ab 2 min „vor 3 min“ */
export function ageText(ageS: number | null): string {
	if (ageS === null) return DASH;
	return ageS < 120 ? `vor ${ageS} s` : `vor ${Math.floor(ageS / 60)} min`;
}
