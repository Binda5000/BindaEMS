// Zahlen und Zeiten für die Anzeige: deutsch (de-AT), Zeiten in Wien, Einheit mit geschütztem
// Leerzeichen; fehlende Werte sind ein Strich, nie 0
import type { Confidence, DrivingSource, DrivingTrip } from './api/schemas';
import { VIENNA, addDays, parseIso, todayVienna } from './time';

export const DASH = '–';
export const NBSP = '\u00a0';

type MaybeNumber = number | null | undefined;

const LOCALE = 'de-AT';
const DIGITS = [0, 1, 2, 3].map(
	(digits) =>
		new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits })
);
const EUR = new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'EUR' });
const TIME = new Intl.DateTimeFormat(LOCALE, {
	timeZone: VIENNA,
	hour: '2-digit',
	minute: '2-digit'
});
const DATE_TIME = new Intl.DateTimeFormat(LOCALE, {
	timeZone: VIENNA,
	day: '2-digit',
	month: '2-digit',
	year: 'numeric',
	hour: '2-digit',
	minute: '2-digit'
});
const DAY = new Intl.DateTimeFormat(LOCALE, {
	timeZone: VIENNA,
	weekday: 'short',
	day: '2-digit',
	month: '2-digit'
});

function isNumber(value: MaybeNumber): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

/** Zahl mit fester Nachkommazahl, ohne „-0“. */
export function formatNumber(value: number, digits: number): string {
	const text = (DIGITS[digits] ?? DIGITS[0]!).format(value);
	return /^-0(,0*)?$/.test(text) ? text.slice(1) : text;
}

/** Zahl mit höchstens `digits` Nachkommastellen („2“, „2,3“), ohne „-0“. */
export function formatNumberMax(value: number, digits: number): string {
	const factor = 10 ** digits;
	const rounded = Math.round(value * factor) / factor;
	const text = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits }).format(rounded);
	return text === '-0' ? '0' : text;
}

function withUnit(value: number, digits: number, unit: string): string {
	return `${formatNumber(value, digits)}${NBSP}${unit}`;
}

export function formatPower(w: MaybeNumber): string {
	if (!isNumber(w)) return DASH;
	if (Math.abs(Math.round(w)) < 1000) return withUnit(w, 0, 'W');
	const kw = w / 1000;
	return withUnit(kw, Math.abs(kw) < 10 ? 2 : 1, 'kW');
}

export function formatEnergy(kwh: MaybeNumber): string {
	if (!isNumber(kwh)) return DASH;
	return withUnit(kwh, Math.abs(kwh) < 10 ? 2 : 1, 'kWh');
}

export function formatCt(ct: MaybeNumber): string {
	return isNumber(ct) ? withUnit(ct, 2, 'ct/kWh') : DASH;
}

/**
 * Betrag in € wie „1.234,50 €“: Zahl mit Tausenderpunkten, geschütztes Leerzeichen, „€“ (de-AT
 * stellt das Zeichen voran, die Anzeige überall dahinter). Ohne „-0“: ein Betrag, der auf 0,00
 * rundet, hat kein Minus.
 */
export function formatEur(eur: MaybeNumber): string {
	if (!isNumber(eur)) return DASH;
	const number = EUR.formatToParts(eur)
		.filter((part) => part.type !== 'currency' && part.type !== 'literal')
		.map((part) => part.value)
		.join('');
	const text = `${number}${NBSP}€`;
	return /[1-9]/.test(number) ? text : text.replace('-', '');
}

/** Ladestand in % (0–100). */
export function formatSoc(pct: MaybeNumber): string {
	return isNumber(pct) ? withUnit(pct, 0, '%') : DASH;
}

/** Anteil (0–1) in %. */
export function formatRatio(ratio: MaybeNumber): string {
	return isNumber(ratio) ? withUnit(ratio * 100, 0, '%') : DASH;
}

function formatInstant(format: Intl.DateTimeFormat, iso: string): string {
	const ms = parseIso(iso);
	return ms === null ? DASH : format.format(ms);
}

/** „10:00“ */
export function formatTime(iso: string): string {
	return formatInstant(TIME, iso);
}

/** „09.10.2026, 10:00“ */
export function formatDateTime(iso: string): string {
	return formatInstant(DATE_TIME, iso);
}

/** „Fr., 09.10.“ für ein Datum `YYYY-MM-DD` (Mittag UTC liegt in Wien immer am selben Tag). */
export function formatDay(date: string): string {
	return formatInstant(DAY, `${date}T12:00:00Z`);
}

// --- Fahrverhalten (Plan 5b) ---------------------------------------------------------------

export const CONFIDENCE_LABELS: Record<Confidence, string> = {
	high: 'hoch',
	medium: 'mittel',
	low: 'niedrig'
};

const CONFIDENCE_RANK: Record<Confidence, number> = { low: 0, medium: 1, high: 2 };

/** Die schwächere von zwei Konfidenzen; `null` zählt nicht, beide `null` ergeben `null`. */
export function weakestConfidence(
	first: Confidence | null,
	second: Confidence | null
): Confidence | null {
	if (first === null) return second;
	if (second === null) return first;
	return CONFIDENCE_RANK[first] <= CONFIDENCE_RANK[second] ? first : second;
}

/** 0 = Montag … 6 = Sonntag (und Feiertag) */
export const WEEKDAY_LABELS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'] as const;

/** Gruppe, aus der das Profil rechnet, wenn ein Wochentag zu wenige Tage hat */
export function weekdayGroup(weekday: number): string {
	if (weekday <= 4) return 'Mo–Fr';
	return weekday === 5 ? 'Sa' : 'So/Feiertag';
}

/** „Mo“ für ein Datum `YYYY-MM-DD` */
export function weekdayOfDate(date: string): string {
	const day = new Date(`${date}T12:00:00Z`).getUTCDay(); // 0 = Sonntag
	return WEEKDAY_LABELS[(day + 6) % 7] ?? DASH;
}

/** „Mo 23.11.“ für ein Datum `YYYY-MM-DD` */
export function weekdayDate(date: string): string {
	const [, month, day] = date.split('-');
	return `${weekdayOfDate(date)} ${day}.${month}.`;
}

/**
 * Die Abfahrt als „07:15“ oder mit Tag als „Mo 23.11. 07:15“ (Wiener Zeit, Plan 5b, Ruling F-3).
 * Den Tag nennt sie, wenn die Absicht einen Abfahrtstag `departureOn` hat, der nicht heute ist;
 * eine tägliche Abfahrt (`null`) bleibt bei der Uhrzeit. Ohne Absicht (`undefined`, etwa im
 * Zeitplan) nennt sie ihn, wenn der Tag nicht das nächste Auftreten der Uhrzeit ist: heute, wenn
 * die Uhrzeit heute noch bevorsteht, sonst morgen (Plan 5b, Ruling F-7).
 */
export function departureText(
	departureAt: string,
	departureOn: string | null | undefined,
	now: Date = new Date()
): string {
	const time = formatTime(departureAt);
	const ms = parseIso(departureAt);
	if (ms === null) return time;
	const day = todayVienna(new Date(ms));
	const today = todayVienna(now);
	const next = time > formatTime(now.toISOString()) ? today : addDays(today, 1);
	const dated =
		departureOn === undefined ? day !== next : departureOn !== null && departureOn !== today;
	return dated ? `${weekdayDate(day)} ${time}` : time;
}

/**
 * Ein Zeitpunkt mit Tag, wenn der nicht heute ist: „17:30“, „morgen 07:15“, sonst „Mi 22.10. 07:15“
 * (Wiener Zeit; heute und morgen zählen nach dem Wiener Kalender von `now`).
 */
export function formatDayTime(at: Date, now: Date = new Date()): string {
	const time = TIME.format(at);
	const day = todayVienna(at);
	const today = todayVienna(now);
	if (day === today) return time;
	return day === addDays(today, 1) ? `morgen ${time}` : `${weekdayDate(day)} ${time}`;
}

/** Woher die Anwesenheit eines Autos kommt; bei Home Assistant mit der eingestellten Entität */
export function drivingSourceText(source: DrivingSource, entity?: string | null): string {
	switch (source) {
		case 'ha':
			return entity ? `Home Assistant (${entity})` : 'Home Assistant';
		case 'home_entity':
			return 'Home Assistant (home_entity)';
		case 'tessie':
			return 'Tessie';
		case 'sessions':
			return 'nur Ansteckvorgänge';
	}
}

/** „Tesla Model 3 erwartet ab 17:30“ (Uhrzeit in Wien) */
export function expectedLabel(name: string, arrivalAt: string): string {
	return `${name} erwartet ab ${formatTime(arrivalAt)}`;
}

/** „Vorschlag: Mo 07:15 · 50 % (Konfidenz hoch)“; ohne Ziel nur Tag und Abfahrt */
export function suggestionText(trip: DrivingTrip): string {
	const confidence = weakestConfidence(
		trip.departure_confidence,
		trip.target_soc_pct === null ? null : trip.target_confidence
	);
	const goal = trip.target_soc_pct === null ? '' : ` · ${formatSoc(trip.target_soc_pct)}`;
	const label = confidence === null ? '' : ` (Konfidenz ${CONFIDENCE_LABELS[confidence]})`;
	return `Vorschlag: ${weekdayOfDate(trip.date)} ${trip.departure}${goal}${label}`;
}
