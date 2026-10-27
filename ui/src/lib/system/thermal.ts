// Zeilen der Karte „Klimaanlage“ auf der System-Seite (Health des core, Plan 6a; Zeile „Plan“
// aus Plan 5c)
import type { IntentPlan, ThermalHealth } from '$lib/api/schemas';
import { reasonLabel } from '$lib/charging/view';
import {
	DASH,
	formatDateTime,
	formatKelvinShift,
	formatNumber,
	formatPower,
	formatTime,
	NBSP
} from '$lib/format';
import { plannedOffsetK, plannedUntil } from '$lib/learning/thermal';

export interface ThermalRow {
	label: string;
	value: string;
}

/** So nah muss das Echo der UVR am gesendeten Versatz liegen, damit es als bestätigt gilt (K) */
const ECHO_TOLERANCE_K = 0.05;

const MODES: Record<string, string> = {
	aus: 'Aus',
	heizen: 'Heizen',
	lueften: 'Lüften',
	kuehlen: 'Kühlen',
	unbekannt: 'unbekannt'
};

/** Versatz in K mit Vorzeichen, Komma und einer Nachkommastelle: „+0,5 K“, „-1,0 K“, „+0,0 K“ */
export function thermalOffset(k: number | null): string {
	if (k === null) return DASH;
	const rounded = Math.round(k * 10) / 10;
	return `${rounded < 0 ? '' : '+'}${formatNumber(rounded, 1)}${NBSP}K`;
}

function degrees(c: number | null): string {
	return c === null ? DASH : `${formatNumber(c, 1)}${NBSP}°C`;
}

/** „-1,0 K (bestätigt)“, wenn das Echo den gesendeten Versatz zeigt, sonst „-1,0 K (Echo +0,0 K)“ */
function offsetText(block: ThermalHealth): string {
	const sent = block.offset_sent_k;
	if (sent === null) return DASH;
	const echo = block.offset_active_k;
	const confirmed = echo !== null && Math.abs(echo - sent) <= ECHO_TOLERANCE_K + 1e-9;
	return `${thermalOffset(sent)} (${confirmed ? 'bestätigt' : `Echo ${thermalOffset(echo)}`})`;
}

function wishText(wish: number | null): string {
	if (wish === null) return DASH;
	if (wish === 0) return 'Stufe 0';
	return `Stufe ${wish > 0 ? '+' : ''}${formatNumber(wish, 0)}`;
}

/**
 * Nach einem Rücksetzen der Strategie bleibt der Grund `thermal_off`, auch wenn die Anlage
 * läuft. Läuft sie (und lüftet nicht) und ist der Wunsch 0, ist „aus oder lüftet“ falsch: dann
 * steht nur, dass der Versatz 0 ist. Ebenso bleibt `thermal_no_echo` stehen, wenn das Echo
 * wieder da ist: Bei Wunsch 0 und einem Echo stimmt „nicht steuerbar“ nicht mehr.
 */
function reasonText(block: ThermalHealth): string {
	if (
		block.reason === 'thermal_off' &&
		block.wish === 0 &&
		block.running === true &&
		block.mode !== 'lueften'
	) {
		return 'Versatz 0';
	}
	if (block.reason === 'thermal_no_echo' && block.wish === 0 && block.offset_active_k !== null) {
		return 'Versatz 0';
	}
	return reasonLabel(block.reason);
}

function modeText(mode: string | null): string {
	return mode === null ? DASH : (MODES[mode] ?? mode);
}

function running(value: boolean | null): string {
	return value === null ? DASH : value ? 'ja' : 'nein';
}

/**
 * Zeile „Plan“: der Planwert des core (`planned`, Stufen) als Versatz des Sollwerts, wie der core
 * ihn sendet (Heizen +0,5 K je Stufe, Kühlen −0,5 K je Stufe, `plannedOffsetK`), bis zum Ende der
 * Slots gleicher Stufen im Plan der Absicht: „+1,0 K bis 14:00“. Ohne diesen Plan oder ohne Slot
 * für jetzt nur der Versatz; ohne Planwert „kein Planwert“.
 */
function planText(
	block: ThermalHealth,
	plan: Pick<IntentPlan, 'valid_until' | 'slots'> | null,
	now: Date
): string {
	const k = plannedOffsetK(block.planned, block.mode);
	if (k === null) return 'kein Planwert';
	const until = plannedUntil(plan, now);
	return until === null ? formatKelvinShift(k) : `${formatKelvinShift(k)} bis ${formatTime(until)}`;
}

/** „Grundlast 400 W · freigebbar 500 W“, ohne Wert und ohne Verbindung „–“ */
export function thermalLoad(block: ThermalHealth): string {
	const base = block.connected ? block.base_w : null;
	const spare = block.connected ? block.spare_w : null;
	return `Grundlast ${formatPower(base)} · freigebbar ${formatPower(spare)}`;
}

/**
 * Die Zeilen der Karte. Ohne frische Werte („nicht verbunden“) gibt es nur den letzten Empfang
 * und den Grund; die übrigen Zahlen wären alt oder leer.
 */
export function thermalRows(
	block: ThermalHealth,
	plan: Pick<IntentPlan, 'valid_until' | 'slots'> | null = null,
	now: Date = new Date()
): ThermalRow[] {
	const received = {
		label: 'Letzter Empfang',
		value: block.last_packet_at === null ? DASH : formatDateTime(block.last_packet_at)
	};
	const reason = { label: 'Grund', value: reasonText(block) };
	const dropped: ThermalRow[] = [
		...((block.foreign_packets ?? 0) > 0
			? [{ label: 'Fremde Pakete', value: String(block.foreign_packets) }]
			: []),
		...((block.bad_packets ?? 0) > 0
			? [{ label: 'Fehlerhafte Pakete', value: String(block.bad_packets) }]
			: []),
		...((block.unit_mismatch ?? 0) > 0
			? [{ label: 'Werte mit falscher Einheit', value: String(block.unit_mismatch) }]
			: [])
	];
	if (!block.connected) return [received, ...dropped, reason];
	return [
		{ label: 'Betriebsart', value: modeText(block.mode) },
		{ label: 'Läuft', value: running(block.running) },
		{ label: 'Ist', value: degrees(block.actual_c) },
		{ label: 'Soll ohne Versatz', value: degrees(block.base_setpoint_c) },
		{ label: 'Versatz', value: offsetText(block) },
		{ label: 'Wunsch', value: wishText(block.wish) },
		{ label: 'Plan', value: planText(block, plan, now) },
		{ label: 'Leistung', value: formatPower(block.power_w) },
		received,
		...dropped,
		reason
	];
}
