// Texte und Ableitungen der Seite „Laden“
import type {
	AssignmentKind,
	ChargingSession,
	ChargeMode,
	Clue,
	EffectiveMode,
	Intent,
	PlanVehicle,
	SocSource,
	SupportSource,
	SurplusPriority,
	VehicleHealth,
	WallboxHealth
} from '$lib/api/schemas';
import {
	DASH,
	departureText,
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
export const CHARGE_MODES: readonly ChargeMode[] = ['pv', 'min_pv', 'now', 'off', 'planned'];

/** Die Modi eines Gastes: ohne „Geplant“, denn für ihn gibt es weder Ladestand noch Abfahrt */
export const GUEST_MODES: readonly ChargeMode[] = ['pv', 'min_pv', 'now', 'off'];

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
	planned: 'Geplant',
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
	mode_planned: 'Geplant',
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
	foreign_change: 'Änderung von außen',
	plan_power: 'Plan: Laden',
	latest_start: 'Späteste Startzeit',
	target_reached: 'Ziel-SOC erreicht',
	charge_limit: 'Ladelimit',
	thermal_more: 'PV-Überschuss in die Klimaanlage',
	thermal_back: 'Netzbezug, Klimaanlage eine Stufe zurück',
	thermal_cars: 'Autos haben Vorrang',
	thermal_battery: 'Akku hat Vorrang',
	thermal_less: 'Teurer Strom, Klimaanlage zurückhaltend',
	thermal_peak: 'Spitze droht, Klimaanlage nicht mehr',
	thermal_mode: 'Betriebsart gewechselt, Versatz 0',
	thermal_stale: 'Keine Werte der Klimaanlage, Versatz 0',
	thermal_no_echo: 'Klimaanlage nicht steuerbar (kein Echo oder gesperrt), Versatz 0',
	thermal_off: 'Klimaanlage aus oder lüftet, Versatz 0'
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

/** Muster einer Abfahrt wie in der Absicht des core: `HH:MM`, 00:00 bis 23:59 */
const DEPARTURE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Ergebnis der Prüfung von Ziel-SOC und Abfahrt: `null` heißt unverändert (nicht mitschicken) */
export type TargetInput =
	| { ok: true; target_soc_pct: number | null; departure: string | null }
	| { ok: false; text: string };

/**
 * Prüft die Felder „Ziel-SOC“ und „Abfahrt“ der Fahrzeugkarte mit den Sätzen der app: Ziel-SOC ist
 * eine ganze Zahl von 20 bis 100, die Abfahrt `HH:MM`. Ein Feld, das dem Stand der Absicht `now`
 * gleicht, kommt als `null` zurück. `badInput`: der Browser hat im Zahlfeld Unsinn gesehen.
 */
export function parseTarget(
	socText: string,
	departure: string,
	now: { target_soc_pct: number; departure: string },
	badInput = false
): TargetInput {
	const pct = Number(socText);
	if (badInput || socText.trim() === '' || !Number.isInteger(pct) || pct < 20 || pct > 100) {
		const shown = Number.isFinite(pct) && socText.trim() !== '' ? formatNumberMax(pct, 1) : socText;
		return { ok: false, text: `Ziel-SOC „${shown}“ liegt außerhalb von 20–100 %.` };
	}
	if (!DEPARTURE.test(departure)) {
		return { ok: false, text: `Abfahrt „${departure}“ ist keine Uhrzeit (HH:MM).` };
	}
	return {
		ok: true,
		target_soc_pct: pct === now.target_soc_pct ? null : pct,
		departure: departure === now.departure ? null : departure
	};
}

/** Was die Fahrzeugkarte und der Zeitplan zu „Geplant“ sagen: die Zeile und die Zusätze */
export interface PlannedLine {
	/** die erste zutreffende Zeile; `null`, wenn keine zutrifft */
	text: string | null;
	notes: string[];
}

export interface PlannedInput {
	/** Lademodus der Absicht; `null`: unbekannt (der Zeitplan kennt nur den Plan) */
	mode: ChargeMode | null;
	/** Health des Fahrzeugs; `null` ohne */
	vehicle: VehicleHealth | null;
	/** das Auto im gültigen Plan; `null`: der Plan nennt es nicht; `undefined`: Plan unbekannt */
	car: PlanVehicle | null | undefined;
	/** Ziel-SOC der Absicht in %; `null` unbekannt */
	target: number | null;
	/**
	 * Abfahrtstag der Absicht (`YYYY-MM-DD`, Plan 5b); `null` täglich; ohne Angabe unbekannt (der
	 * Zeitplan kennt nur den Plan). Bestimmt, ob die Zeile den Tag der Abfahrt nennt.
	 */
	departureOn?: string | null;
}

function known(source: SocSource | null): boolean {
	return source === 'measured' || source === 'estimated';
}

/** Die Zeile, die zuerst zutrifft (Texte von Plan 4b); `null`, wenn keine zutrifft */
function plannedText(
	vehicle: VehicleHealth | null,
	car: PlanVehicle | null | undefined,
	source: SocSource | null,
	departureOn: string | null | undefined
): string | null {
	const planned = vehicle?.planned ?? null;
	const soc = vehicle?.soc_pct ?? null;
	if (planned?.state === 'reached' && known(source) && soc !== null) {
		return `Ziel erreicht (${formatSoc(soc)}).`;
	}
	if (car === undefined) return null; // ohne Plan weder „Fertig um“ noch „Kein Plan“
	if (car !== null) {
		const goal = `Ziel ${formatSoc(car.target_soc_pct)} bis ${departureText(car.departure_at, departureOn)}`;
		if (!car.reachable) {
			return `${goal} nicht erreichbar, voraussichtlich ${formatSoc(car.expected_soc_pct)}.`;
		}
		if (car.finish_at !== null) return `Fertig um ${formatTime(car.finish_at)} · ${goal}`;
		if (!car.soc_assumed && car.soc_start_pct >= car.target_soc_pct) {
			return `Ziel erreicht (${formatSoc(car.soc_start_pct)}).`;
		}
		return null; // der Plan lädt das Auto in seinem Horizont nicht
	}
	if (planned?.latest_start != null) {
		return `Kein Plan · lädt spätestens ab ${formatTime(planned.latest_start)}`;
	}
	return null;
}

/** So weit dürfen das wirksame Ziel in Health und im Plan auseinanderliegen (Rundung) in % */
const TARGET_TOLERANCE_PCT = 0.05;

/**
 * Passt das Auto im Plan zu „Geplant“ in Health (wirksames Ziel und Abfahrt)? Nach einer Änderung
 * meldet Health das neue Ziel, bevor die app neu geplant hat. Ohne Stand in Health gilt der Plan.
 */
export function planFits(vehicle: VehicleHealth | null, car: PlanVehicle): boolean {
	const planned = vehicle?.planned ?? null;
	if (planned === null) return true;
	return (
		Math.abs(planned.target_soc_pct - car.target_soc_pct) <= TARGET_TOLERANCE_PCT &&
		parseIso(planned.departure_at) === parseIso(car.departure_at)
	);
}

/**
 * Der Stand von „Geplant“ wie „Fertig um“ und „Ziel erreichbar“ in Home Assistant: Meldet Health
 * das Ziel erreicht (mit bekanntem Ladestand), gilt das; sonst gilt das Auto im gültigen Plan, ohne
 * Plan die späteste Startzeit aus Health. Dazu die Quelle des Ladestands (aus Health, ohne Health
 * aus dem Plan; angenommen heißt nie gemessen) und ein Ladelimit unter dem Ziel. `null` außerhalb
 * von „Geplant“, für ein abgestecktes Auto und ohne Stand von „Geplant“ in Health und Plan.
 */
export function plannedLine({
	mode,
	vehicle,
	car,
	target,
	departureOn
}: PlannedInput): PlannedLine | null {
	if (mode !== null && mode !== 'planned') return null;
	if (vehicle?.plugged === false) return null;
	if (!vehicle?.planned && !car) return null;
	const source = vehicle?.soc_source ?? (car?.soc_assumed ? 'assumed' : null);
	const notes: string[] = [];
	if (source === 'estimated') notes.push('Ladestand geschätzt');
	if (source === 'assumed') notes.push(`Ladestand unbekannt, gerechnet ab ${formatSoc(0)}.`);
	// ohne Angleichen rechnen Plan und core mit dem Limit als Ziel: das wirksame Ziel liegt darunter
	const limit = vehicle?.charge_limit_pct ?? car?.limit_pct ?? null;
	const effective = vehicle?.planned?.target_soc_pct ?? car?.target_soc_pct ?? null;
	if (
		limit !== null &&
		target !== null &&
		effective !== null &&
		effective < target &&
		limit < target
	) {
		notes.push(`Ladelimit im Auto ${formatSoc(limit)} liegt unter dem Ziel.`);
	}
	return { text: plannedText(vehicle, car, source, departureOn), notes };
}
