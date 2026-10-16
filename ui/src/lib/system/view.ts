// Texte und Zeilen der System-Seite
import type { CoreHealth, CoreState, Intent, PriceStatus, Reading } from '$lib/api/schemas';
import { DASH, formatNumber } from '$lib/format';
import { parseIso } from '$lib/time';

/** Aktor-ID von Victron (`VICTRON` in `shared/control.py`) */
const VICTRON = 'victron';

type Watchdog = CoreHealth['watchdog'];
type Actuator = CoreHealth['actuators'][number];

const COMPONENTS: Record<string, string> = {
	core: 'core',
	prices: 'Preise',
	forecast: 'PV-Prognose',
	'load-forecast': 'Lastprognose',
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
	multi_mode: 'Multi-Modus',
	import_limit: 'Importlimit',
	fresh_data: 'Datenfrische'
};

const MODES: Record<CoreHealth['mode'], string> = {
	START: 'Start',
	OBSERVE: 'Beobachten (nur lesend)',
	CONTROL: 'Regeln',
	DEGRADED: 'Eingeschränkt',
	SAFE: 'Sicherer Zustand'
};

const WATCHDOG_STATES: Record<Watchdog['state'], string> = {
	waiting: 'wartet',
	armed: 'scharf',
	tripped: 'ausgelöst',
	disabled: 'deaktiviert',
	unknown: 'keine Antwort'
};

const HEARTBEATS: Record<Watchdog['heartbeat'], string> = {
	off: 'aus (kein Aktor live)',
	active: 'aktiv',
	released: 'freigegeben'
};

const ACTUATOR_MODES: Record<Actuator['requested'], string> = {
	dry_run: 'Dry-Run',
	live: 'live'
};

const SIGNAL_NUMBER = new Intl.NumberFormat('de-AT', { maximumFractionDigits: 3 });

export function componentLabel(name: string): string {
	return COMPONENTS[name] ?? name;
}

export function selfcheckLabel(id: string): string {
	if (id.startsWith('evcs_mode.')) return `EVCS-Modus ${id.slice('evcs_mode.'.length)}`;
	return SELFCHECKS[id] ?? id;
}

export function modeLabel(mode: CoreHealth['mode']): string {
	return MODES[mode];
}

export function watchdogStateLabel(state: Watchdog['state']): string {
	return WATCHDOG_STATES[state];
}

export function heartbeatLabel(heartbeat: Watchdog['heartbeat']): string {
	return HEARTBEATS[heartbeat];
}

export function actuatorModeLabel(mode: Actuator['requested']): string {
	return ACTUATOR_MODES[mode];
}

/** Die Version des Watchdogs am Cerbo; weicht sie von der erwarteten ab, steht diese dabei. */
export function watchdogVersionText(version: string | null, expected: string): string {
	if (version === null) return DASH;
	return version === expected ? version : `${version} (erwartet ${expected})`;
}

/** Hinweise zu einem Aktor: warum er nicht live läuft, dann wodurch er gesperrt ist */
export function actuatorNotes(actuator: Pick<Actuator, 'reasons' | 'locked'>): string[] {
	return [...actuator.reasons, ...actuator.locked.map((reason) => `gesperrt: ${reason}`)];
}

export interface LiveCondition {
	label: string;
	met: boolean;
}

/**
 * Die Bedingungen für den Live-Betrieb eines Aktors (Spec 8.7): Freigabe, Watchdog (nur wo der
 * Betrieb ihn braucht), Regelbetrieb und keine Sperre. Hinweise, keine Sperre des Dialogs: der
 * core wartet von selbst, bis sie erfüllt sind.
 */
export function liveConditions(
	actuator: Actuator,
	health: Pick<CoreHealth, 'watchdog'>,
	intent: Pick<Intent, 'control'> | null
): LiveCondition[] {
	return [
		{ label: 'live erlaubt', met: actuator.live_allowed },
		...(actuator.watchdog_needed ? [{ label: 'Watchdog scharf', met: health.watchdog.armed }] : []),
		{ label: 'Regelbetrieb an', met: intent?.control ?? false },
		{ label: 'nicht gesperrt', met: actuator.locked.length === 0 }
	];
}

/**
 * Alle Aktoren außer Victron (EVCS, Tesla) übernimmt der core von evcc. Ihre IDs setzen sich
 * aus den Schlüsseln der Konfiguration zusammen (`wallbox.<key>`, `vehicle.<key>`); fest ist nur
 * `victron`. Ein unbekannter Aktor verlangt darum im Zweifel die Bestätigung.
 */
export function needsEvccConfirmation(actuatorIds: string[]): boolean {
	return actuatorIds.some((id) => id !== VICTRON);
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
