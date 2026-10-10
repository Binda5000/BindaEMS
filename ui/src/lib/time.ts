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

/** Zeitpunkt, zu dem die Wiener Uhr am Tag `date` die volle Stunde `hour` zeigt; null in der Lücke. */
function localHour(date: string, hour: number): number | null {
	const [year, month, day] = splitDate(date);
	const wall = Date.UTC(year, month - 1, day, hour);
	const first = wall - offsetMinutes(wall) * 60_000;
	const at = wall - offsetMinutes(first) * 60_000;
	return at + offsetMinutes(at) * 60_000 === wall ? at : null;
}

// Abstände der Achsenmarken in Minuten; ab einem Tag an Wiener Mitternächten
const TICK_STEPS_MIN = [
	5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080, 20160, 43200, 86400, 129600, 259200,
	525600
];

/**
 * Marken für eine Zeitachse an vollen Wiener Minuten, Stunden bzw. Mitternächten, höchstens
 * `maxTicks`. ECharts legt seine Marken in die Zeitzone des Browsers; so stimmen sie auch
 * anderswo und an Tagen der Zeitumstellung mit der Wiener Uhr überein.
 */
export function viennaTicks(fromMs: number, toMs: number, maxTicks: number): number[] {
	if (!(toMs > fromMs)) return [];
	const spanMin = (toMs - fromMs) / 60_000;
	const step = TICK_STEPS_MIN.find((minutes) => spanMin / minutes + 1 <= maxTicks) ?? 525600;
	const ticks: number[] = [];
	const push = (at: number | null) => {
		if (at !== null && at >= fromMs && at <= toMs) ticks.push(at);
	};
	const last = todayVienna(new Date(toMs));
	for (let date = todayVienna(new Date(fromMs)); date <= last; date = addDays(date, 1)) {
		if (step < 1440) {
			// innerhalb einer Stunde ändert sich der Versatz nie (Umstellung zur vollen Stunde)
			for (let hour = 0; hour < 24; hour += Math.max(1, step / 60)) {
				const base = localHour(date, hour);
				if (base === null) continue;
				for (let minute = 0; minute < 60; minute += step < 60 ? step : 60) {
					push(base + minute * 60_000);
				}
			}
			continue;
		}
		// mehrtägig: ab dem 1.1.1970 gezählt, damit die Marken beim Verschieben stehen bleiben;
		// Wochen beginnen am Montag
		const [year, month, day] = splitDate(date);
		const dayNumber = Date.UTC(year, month - 1, day) / 86_400_000 + (step === 10080 ? 3 : 0);
		if (dayNumber % (step / 1440) === 0) push(localMidnight(date));
	}
	return ticks;
}
