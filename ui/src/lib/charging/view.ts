// Texte und Ableitungen der Seite „Laden“
import type {
	AssignmentKind,
	ChargingSession,
	ChargeMode,
	Clue,
	EffectiveMode,
	Intent,
	SupportSource,
	SurplusPriority,
	VehicleHealth,
	WallboxHealth
} from '$lib/api/schemas';
import {
	DASH,
	formatEur,
	formatNumber,
	formatNumberMax,
	formatSoc,
	formatTime,
	NBSP
} from '$lib/format';
import { parseIso, todayVienna } from '$lib/time';

/** Schlüssel eines Fahrzeugs → Anzeigename */
export type Names = Record<string, string>;

/** Die Modi, die die Bedienung einstellt, in der Reihenfolge der Auswahl */
export const CHARGE_MODES: readonly ChargeMode[] = ['pv', 'min_pv', 'now', 'off'];

export const SURPLUS_KINDS: readonly SurplusPriority['kind'][] = [
	'cars_first',
	'battery_first',
	'battery_until_soc'
];

const MODES: Record<EffectiveMode, string> = {
	pv: 'PV-Überschuss',
	min_pv: 'Min+PV',
	now: 'Sofort',
	off: 'Aus',
	boost: 'Boost'
};

const ASSIGNMENTS: Record<AssignmentKind, string> = {
	detected: 'erkannt',
	default: 'angenommen',
	override: 'Übersteuerung',
	unclear: 'unklar'
};

/** Satz zum Indiz: [stützt, schließt aus] */
const CLUES: Record<Clue, readonly [string, string]> = {
	tessie: ['Tessie: zu Hause und angesteckt', 'Tessie: nicht angesteckt oder nicht zu Hause'],
	current: ['Strom passt zu Tessie', 'Strom passt nicht zu Tessie'],
	timing: ['Angesteckt, als der Vorgang begann', 'Angesteckt zu anderer Zeit'],
	phases: ['Phasenzahl passt', 'Phasenzahl passt nicht'],
	home: ['Home Assistant: zu Hause', 'Home Assistant: nicht zu Hause'],
	elsewhere: ['Angesteckt, andere Wallbox leer', 'Andere Wallbox belegt']
};

/** Gründe des letzten Befehls wie im Entscheidungslog des core (`_REASON_TEXT`) */
const REASONS: Record<string, string> = {
	safe_state: 'sicherer Zustand',
	handback: 'Übergabe',
	grid_outage: 'Netzausfall',
	mode_pv: 'PV-Überschuss',
	mode_min_pv: 'Min+PV',
	mode_now: 'Sofort',
	mode_off: 'Aus',
	boost: 'Boost',
	pv_surplus: 'Überschuss',
	phase_limit: 'Phasengrenze',
	import_limit: 'Importlimit',
	reserve_guard: 'Schutz der USV-Reserve',
	overload: 'Überlast',
	wake: 'Wecken',
	not_taken_retry: 'erneut nach „nicht übernommen“',
	assignment_unclear: 'Zuordnung unklar',
	resume_after_outage: 'Wiederaufnahme nach Ausfall',
	battery_comp: 'Akku nicht ins Auto',
	battery_support: 'Akku-Unterstützung',
	peak_guard: '15-min-Wächter',
	plan_hold: 'Plan: Halten',
	plan_grid_charge: 'Plan: Netzladen',
	assignment_detected: 'Zuordnung erkannt',
	self_start: 'Selbststart',
	foreign_change: 'Änderung von außen'
};

const SURPLUS: Record<'cars_first' | 'battery_first', string> = {
	cars_first: 'Autos zuerst',
	battery_first: 'Akku zuerst'
};

export function modeLabel(mode: EffectiveMode | null): string {
	return mode === null ? DASH : MODES[mode];
}

export function assignmentLabel(kind: AssignmentKind | null): string {
	return kind === null ? DASH : ASSIGNMENTS[kind];
}

export function reasonLabel(code: string | null): string {
	return code === null ? DASH : (REASONS[code] ?? code);
}

/** „Tesla Model 3: Strom passt zu Tessie“ */
export function indicatorText(
	indicator: { vehicle: string; clue: Clue; supports: boolean },
	names: Names
): string {
	const [supports, excludes] = CLUES[indicator.clue];
	return `${names[indicator.vehicle] ?? indicator.vehicle}: ${indicator.supports ? supports : excludes}`;
}

/** Wer an der Wallbox steht: Name des Fahrzeugs, „Gast“ oder „kein Fahrzeug“ */
export function occupantText(wallbox: WallboxHealth, names: Names): string {
	if (wallbox.occupant === null) return 'kein Fahrzeug';
	if (wallbox.occupant === 'guest') return 'Gast';
	return names[wallbox.occupant] ?? wallbox.name ?? wallbox.occupant;
}

export function surplusLabel(priority: SurplusPriority): string {
	if (priority.kind === 'battery_until_soc') {
		return `Akku bis ${formatSoc(priority.soc_pct)}, dann Autos`;
	}
	return SURPLUS[priority.kind];
}

const SUPPORT_SOURCES: Record<SupportSource, string> = {
	intent: 'Einstellung',
	config: 'Konfiguration',
	plan: 'Plan'
};

/** Die wirksame Schwelle der Akku-Unterstützung (Health) und woher sie kommt */
export interface SupportEffective {
	soc_pct: number;
	source: SupportSource;
}

export function supportSourceLabel(source: SupportSource): string {
	return SUPPORT_SOURCES[source];
}

/** „Wirksam ab 82 % (Plan).“; ohne Health ist die Schwelle unbekannt */
export function supportHint(effective: SupportEffective | null): string {
	if (effective === null) return 'Wirksame Schwelle unbekannt.';
	return `Wirksam ab ${formatSoc(effective.soc_pct)} (${supportSourceLabel(effective.source)}).`;
}

/** Ergebnis der Prüfung des Zahlfelds: `soc_pct` `null` heißt „Schwelle der Konfiguration“ */
export type SupportThreshold = { ok: true; soc_pct: number | null } | { ok: false; text: string };

/**
 * Prüft die Eingabe der Unterstützungsschwelle: leer ist erlaubt, sonst gilt USV-Reserve bis 100 %.
 * `text` ist der Inhalt des Zahlfelds; ein Browser gibt bei Unsinn einen leeren Text zurück, dann
 * ist `badInput` wahr.
 */
export function parseSupportThreshold(
	text: string,
	reservePct: number,
	badInput = false
): SupportThreshold {
	if (badInput) return { ok: false, text: 'Unterstützungsschwelle ist keine Zahl.' };
	if (text.trim() === '') return { ok: true, soc_pct: null };
	const pct = Number(text);
	if (Number.isFinite(pct) && pct >= reservePct && pct <= 100) return { ok: true, soc_pct: pct };
	const shown = Number.isFinite(pct) ? formatNumberMax(pct, 1) : text;
	return {
		ok: false,
		text: `Unterstützungsschwelle „${shown}“ liegt außerhalb von ${formatNumberMax(reservePct, 1)}–100 %.`
	};
}

function swap(order: readonly string[], index: number, other: number): string[] {
	const result = [...order];
	if (index < 0 || other < 0 || other >= result.length) return result;
	[result[index], result[other]] = [result[other]!, result[index]!];
	return result;
}

export function moveUp(order: readonly string[], key: string): string[] {
	const index = order.indexOf(key);
	return swap(order, index, index - 1);
}

export function moveDown(order: readonly string[], key: string): string[] {
	const index = order.indexOf(key);
	return swap(order, index, index + 1);
}

/** Reihenfolge der Fahrzeuge: die der Absicht (nur bekannte), fehlende hinten, sonst die der Konfiguration */
export function priorityOrder(
	priority: readonly string[] | null,
	keys: readonly string[]
): string[] {
	const known = (priority ?? []).filter((key) => keys.includes(key));
	return [...known, ...keys.filter((key) => !known.includes(key))];
}

export interface BoostInfo {
	active: boolean;
	/** bis wann der Boost gilt (laut Health, bei wartendem Boost laut Absicht) */
	until: string | null;
	/** der Boost gilt, wartet aber auf das Anstecken */
	pending: boolean;
}

/**
 * Beschreibt Health die gültige Absicht? Nur dann, wenn es ihre Version (oder eine neuere) schon
 * übernommen hat; sonst zeigt Health den Stand davor, und die Anzeige folgt der Absicht.
 */
export function healthIsCurrent(healthVersion: number | null, intent: Intent | null): boolean {
	return intent === null || (healthVersion !== null && healthVersion >= intent.version);
}

function futureBoost(vehicle: VehicleHealth, intent: Intent | null, nowMs: number): string | null {
	const wanted = intent?.vehicles[vehicle.id]?.boost_until ?? null;
	const end = wanted === null ? null : parseIso(wanted);
	return wanted !== null && end !== null && end > nowMs ? wanted : null;
}

/**
 * Health zeigt den Boost; wartet er auf das Anstecken, steht er nur in der Absicht. Ist Health
 * älter als die Absicht (`current` falsch), gilt die Absicht: ein Boost in der Zukunft wird gezeigt,
 * bis Health ihn bestätigt, ein beendeter nicht mehr.
 */
export function boostInfo(
	vehicle: VehicleHealth,
	intent: Intent | null,
	nowMs: number,
	current = true
): BoostInfo {
	const wanted = futureBoost(vehicle, intent, nowMs);
	if (!current) {
		if (wanted === null) return { active: false, until: null, pending: false };
		return { active: true, until: vehicle.boost_until ?? wanted, pending: vehicle.boost_pending };
	}
	if (vehicle.boost_until !== null) {
		return { active: true, until: vehicle.boost_until, pending: vehicle.boost_pending };
	}
	if (vehicle.boost_pending && wanted !== null) {
		return { active: true, until: wanted, pending: true };
	}
	return { active: false, until: null, pending: false };
}

/**
 * Das Fahrzeug, das auf diese Wallbox übersteuert ist: Die Absicht nennt die Wallbox, und Health
 * bestätigt die Übersteuerung oder kennt sie noch nicht (wartend, Stand der Regelung unbekannt).
 * Eine beendete Übersteuerung bleibt in der Absicht stehen, gilt aber nicht. Ist Health älter als
 * die Absicht (`current` falsch), gilt allein die Absicht.
 */
export function overrideVehicle(
	wallbox: string,
	vehicles: readonly VehicleHealth[],
	intent: Intent | null,
	current = true
): string | null {
	for (const vehicle of vehicles) {
		if (intent?.vehicles[vehicle.id]?.wallbox !== wallbox) continue;
		const active = vehicle.assignment === 'override' && vehicle.wallbox === wallbox;
		if (!current || active || vehicle.override_pending || vehicle.mode === null) return vehicle.id;
	}
	return null;
}

/** Meldung an einer Karte: Ablehnung des core (Fehler) oder Begrenzung (Warnung) */
export interface CardNotice {
	level: 'error' | 'warning';
	text: string;
}

/** Beendet ist eine Session mit Ende, die nicht mehr offen ist; sonst läuft sie. */
function isFinished(session: ChargingSession): session is ChargingSession & { end: string } {
	return !session.open && session.end !== null;
}

function isRunning(session: ChargingSession): boolean {
	return !isFinished(session);
}

function startMs(session: ChargingSession): number {
	return parseIso(session.start) ?? Number.NEGATIVE_INFINITY;
}

/**
 * Die Session, die die Fahrzeugkarte zeigt: die offene (bei mehreren die jüngste), sonst die
 * jüngste beendete des Fahrzeugs. Ohne Session `null`; die Reihenfolge der Liste gilt nicht.
 */
export function latestSession(
	sessions: readonly ChargingSession[],
	vehicle: string
): ChargingSession | null {
	let best: ChargingSession | null = null;
	for (const session of sessions) {
		if (session.vehicle !== vehicle) continue;
		if (
			best === null ||
			(isRunning(session) && !isRunning(best)) ||
			(isRunning(session) === isRunning(best) && startMs(session) > startMs(best))
		) {
			best = session;
		}
	}
	return best;
}

/** „08.10.“ für einen Zeitstempel, nach Wiener Zeit */
function dayMonth(iso: string): string {
	const ms = parseIso(iso);
	if (ms === null) return DASH;
	const [, month, day] = todayVienna(new Date(ms)).split('-');
	return `${day}.${month}.`;
}

function sameDay(first: string, second: string): boolean {
	const a = parseIso(first);
	const b = parseIso(second);
	return a !== null && b !== null && todayVienna(new Date(a)) === todayVienna(new Date(b));
}

/**
 * Die Zeile der Fahrzeugkarte. Läuft die Session: „Session seit 08:45 · 1,4 kWh (PV 0,5 · Akku 0,2
 * · Netz 0,7)“. Danach: „Letzte Session 08.10. 18:00–21:30 · 12,4 kWh · 0,52 €“, die Kosten nur,
 * wenn sie bekannt sind; endet die Session an einem anderen Tag, steht dort das Datum des Endes.
 */
export function sessionLine(session: ChargingSession): string {
	const energy = `${formatNumber(session.kwh, 1)}${NBSP}kWh`;
	if (!isFinished(session)) {
		const split = [
			['PV', session.pv_kwh],
			['Akku', session.battery_kwh],
			['Netz', session.grid_kwh]
		] as const;
		const parts = split.map(([label, kwh]) => `${label} ${formatNumber(kwh, 1)}`).join(' · ');
		return `Session seit ${formatTime(session.start)} · ${energy} (${parts})`;
	}
	const end = sameDay(session.start, session.end)
		? formatTime(session.end)
		: `${dayMonth(session.end)} ${formatTime(session.end)}`;
	const cost = session.cost_eur === null ? '' : ` · ${formatEur(session.cost_eur)}`;
	return `Letzte Session ${dayMonth(session.start)} ${formatTime(session.start)}–${end} · ${energy}${cost}`;
}
