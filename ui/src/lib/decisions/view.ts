// Texte, Filter und Zeitraum der Seite „Entscheidungen“ und der Karte „Warum?“
import type { DecisionQuery, DecisionResult } from '$lib/api/schemas';
import { localDayRange, parseIso, todayVienna } from '$lib/time';

/** Einträge je Seite; „Ältere laden“ holt die nächsten */
export const PAGE_SIZE = 100;

const RESULTS: Record<DecisionResult, string> = {
	ok: 'bestätigt',
	error: 'Fehler',
	timeout: 'nicht bestätigt',
	not_sent: 'nicht gesendet',
	dry_run: 'Dry-Run',
	rejected: 'abgelehnt',
	deferred: 'zurückgestellt',
	foreign: 'von außen geändert'
};

/** Die Ergebnisse in der Reihenfolge der Auswahl */
export const RESULT_IDS = Object.keys(RESULTS) as DecisionResult[];

export function resultLabel(result: DecisionResult): string {
	return RESULTS[result];
}

export type RangeId = 'hour' | 'today' | 'week';
export type Range = RangeId | { date: string };

export const RANGES: readonly { id: RangeId; label: string }[] = [
	{ id: 'hour', label: 'Letzte Stunde' },
	{ id: 'today', label: 'Heute' },
	{ id: 'week', label: '7 Tage' }
];

const HOUR_MS = 3_600_000;
const WEEK_MS = 7 * 24 * HOUR_MS;

/**
 * Zeitraum als `since` (und `until`) der Abfrage: Stunde und Woche zählen von `now` zurück und
 * haben kein Ende, „heute“ beginnt um 0 Uhr Wiener Zeit, ein Datum ist der Wiener Tag.
 */
export function rangeQuery(range: Range, now: Date): { since: string; until?: string } {
	if (range === 'hour') return { since: new Date(now.getTime() - HOUR_MS).toISOString() };
	if (range === 'week') return { since: new Date(now.getTime() - WEEK_MS).toISOString() };
	if (range === 'today') return { since: localDayRange(todayVienna(now)).from };
	const { from, to } = localDayRange(range.date);
	return { since: from, until: to };
}

/** Die Filter der Seite, so wie die URL sie trägt (`range` ist `hour`, `today`, `week` oder ein Datum) */
export interface Filters {
	range: string;
	actuator: string;
	result: DecisionResult | '';
	dryRun: boolean;
	text: string;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DEFAULT_RANGE: RangeId = 'today';

export function isDate(range: string): boolean {
	return DATE.test(range);
}

function parseRange(range: string): Range {
	if (DATE.test(range)) return { date: range };
	return RANGES.some((item) => item.id === range) ? (range as RangeId) : DEFAULT_RANGE;
}

export function filtersFromParams(params: URLSearchParams): Filters {
	const range = params.get('range') ?? '';
	const result = params.get('result') ?? '';
	return {
		range: isDate(range) || RANGES.some((item) => item.id === range) ? range : DEFAULT_RANGE,
		actuator: params.get('actuator') ?? '',
		result: (RESULT_IDS as string[]).includes(result) ? (result as DecisionResult) : '',
		dryRun: params.get('dry_run') !== 'false',
		text: params.get('q') ?? ''
	};
}

/** Nur was vom Standard abweicht, damit die URL kurz bleibt */
export function filtersToParams(filters: Filters): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.range !== DEFAULT_RANGE) params.set('range', filters.range);
	if (filters.actuator !== '') params.set('actuator', filters.actuator);
	if (filters.result !== '') params.set('result', filters.result);
	if (!filters.dryRun) params.set('dry_run', 'false');
	if (filters.text !== '') params.set('q', filters.text);
	return params;
}

/** Abfrage der ersten Seite; `dry_run` geht nur bei „aus“ mit (der Server zeigt sie sonst) */
export function decisionQuery(filters: Filters, now: Date): DecisionQuery {
	const query: DecisionQuery = { ...rangeQuery(parseRange(filters.range), now) };
	if (filters.actuator !== '') query.actuator = filters.actuator;
	if (filters.result !== '') query.result = filters.result;
	if (!filters.dryRun) query.dry_run = false;
	if (filters.text !== '') query.text = filters.text;
	query.limit = PAGE_SIZE;
	return query;
}

/** Alter einer Entscheidung in Sekunden; ein Zeitstempel nach `nowMs` zählt als 0 */
export function decisionAgeS(ts: string, nowMs: number): number | null {
	const ms = parseIso(ts);
	return ms === null ? null : Math.max(0, Math.round((nowMs - ms) / 1000));
}

/**
 * Der Rest von `text` ohne den Namen am Anfang, wenn der Text mit `name` beginnt und danach ein
 * Leerzeichen, `(` oder `:` folgt (ohne führendes Leerzeichen); sonst `null`.
 */
export function splitName(text: string, name: string): string | null {
	if (name === '' || !text.startsWith(name)) return null;
	const rest = text.slice(name.length);
	if (rest.startsWith(' ')) return rest.trimStart();
	return rest.startsWith('(') || rest.startsWith(':') ? rest : null;
}

/** Ergebnisse, die die Karte „Warum?“ mit der Warnfarbe zeigt */
const WARN_RESULTS: readonly DecisionResult[] = [
	'deferred',
	'rejected',
	'error',
	'timeout',
	'not_sent',
	'foreign'
];

export function isWarnResult(result: DecisionResult): boolean {
	return WARN_RESULTS.includes(result);
}

// `{ (Gründe)}: {Befehl} – {Ergebnis}.{ Befunde}` nach dem Namen (`decision_text` in shared/decisions.py)
const BODY = /^(?:\((.+?)\))?: (.+?) – (.*)$/s;
// Ergebnisse mit Detail: „Fehler: …“, „nicht gesendet: …“, „von außen geändert: …“
const RESULT_DETAIL = /^(?:Fehler|nicht gesendet|von außen geändert): (.+)$/s;

/**
 * Der Satz einer Entscheidung für die Karte „Warum?“: ohne Namen am Anfang und ohne Ergebnis,
 * `{Gründe}: {Befehl}` (ohne Gründe nur `{Befehl}`), das Detail eines Ergebnisses („Fehler: X“)
 * und Befunde dahinter mit „ · “. Das Ergebnis ohne Detail zeigt das Etikett der Karte. Folgt der
 * Text dem Muster nicht, bleibt der Rest nach dem Namen (oder der ganze Text) unverändert.
 */
export function whyBody(text: string, name: string): string {
	const rest = splitName(text, name);
	if (rest === null) return text;
	const match = BODY.exec(rest);
	if (match === null) return rest;
	const [, reasons, command, tail = ''] = match;
	// Das Ergebnis endet am ersten Punkt mit Leerzeichen; was folgt, sind die Befunde
	const end = tail.indexOf('. ');
	const result = (end === -1 ? tail : tail.slice(0, end)).replace(/\.$/, '');
	const findings = end === -1 ? '' : tail.slice(end + 2).trim();
	const body = reasons ? `${reasons}: ${command}` : (command ?? '');
	const detail = RESULT_DETAIL.exec(result)?.[1];
	return [body, detail, findings].filter((part) => part).join(' · ');
}
