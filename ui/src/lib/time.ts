// Zeiten: intern UTC, Tage und Anzeige immer in Wien (wie die app, Spec 5.5)

export const VIENNA = 'Europe/Vienna';

const DATE_PARTS = new Intl.DateTimeFormat('en-GB', {
	timeZone: VIENNA,
	year: 'numeric',
	month: '2-digit',
	day: '2-digit'
});
const OFFSET = new Intl.DateTimeFormat('en-US', { timeZone: VIENNA, timeZoneName: 'longOffset' });

/** Epoch-ms eines Zeitstempels vom Server; der liefert Mikrosekunden, Safari liest sicher nur drei Stellen. */
export function parseIso(iso: string): number | null {
	const ms = Date.parse(iso.replace(/(\.\d{3})\d+/, '$1'));
	return Number.isNaN(ms) ? null : ms;
}

function part(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
	return parts.find((p) => p.type === type)?.value ?? '';
}

/** Heutiges Datum in Wien als `YYYY-MM-DD`. */
export function todayVienna(now: Date = new Date()): string {
	const parts = DATE_PARTS.formatToParts(now);
	return `${part(parts, 'year')}-${part(parts, 'month')}-${part(parts, 'day')}`;
}

function splitDate(date: string): [number, number, number] {
	const [year, month, day] = date.split('-').map(Number);
	return [year ?? Number.NaN, month ?? Number.NaN, day ?? Number.NaN];
}

/** Kalendertage addieren (ohne Uhrzeit, also ohne Zeitumstellung). */
export function addDays(date: string, days: number): string {
	const [year, month, day] = splitDate(date);
	return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/** Versatz von Wien gegenüber UTC in Minuten zum Zeitpunkt `ms`. */
function offsetMinutes(ms: number): number {
	const name = part(OFFSET.formatToParts(ms), 'timeZoneName');
	const match = /GMT([+-])(\d{2}):?(\d{2})?/.exec(name);
	if (!match) return 0; // „GMT“ ohne Versatz
	const minutes = Number(match[2]) * 60 + Number(match[3] ?? 0);
	return match[1] === '-' ? -minutes : minutes;
}

/** Mitternacht des lokalen Tages in Epoch-ms; zwei Durchläufe genügen an Umstellungstagen. */
function localMidnight(date: string): number {
	const [year, month, day] = splitDate(date);
	const guess = Date.UTC(year, month - 1, day);
	const first = guess - offsetMinutes(guess) * 60_000;
	return guess - offsetMinutes(first) * 60_000;
}

/** Ein lokaler Tag als UTC-Grenzen (23, 24 oder 25 h). */
export function localDayRange(date: string): { from: string; to: string } {
	return localRange(date, date);
}

/** Vom Beginn des ersten bis zum Ende des letzten lokalen Tages. */
export function localRange(first: string, last: string): { from: string; to: string } {
	return {
		from: new Date(localMidnight(first)).toISOString(),
		to: new Date(localMidnight(addDays(last, 1))).toISOString()
	};
}
