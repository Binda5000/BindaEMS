// Zahlen und Zeiten für die Anzeige: deutsch (de-AT), Zeiten in Wien, Einheit mit geschütztem
// Leerzeichen; fehlende Werte sind ein Strich, nie 0
import { VIENNA, parseIso } from './time';

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

/** Betrag in €, ohne „-0“: ein Betrag, der auf 0,00 rundet, hat kein Minus. */
export function formatEur(eur: MaybeNumber): string {
	if (!isNumber(eur)) return DASH;
	const text = EUR.format(eur);
	return /[1-9]/.test(text) ? text : text.replace('-', '');
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
