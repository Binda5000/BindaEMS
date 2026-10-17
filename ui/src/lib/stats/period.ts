// Zeiträume der Statistik: Überschrift, Blättern und URL-Parameter. Alles mit reinen
// Datumstexten `JJJJ-MM-TT` (kalendarisch, ohne Uhrzeit), damit die Zeitumstellung nie stört.
import { addDays, todayVienna } from '$lib/time';

export type Period = 'day' | 'week' | 'month' | 'year';

export const PERIODS: readonly { id: Period; label: string; param: string }[] = [
	{ id: 'day', label: 'Tag', param: 'tag' },
	{ id: 'week', label: 'Woche', param: 'woche' },
	{ id: 'month', label: 'Monat', param: 'monat' },
	{ id: 'year', label: 'Jahr', param: 'jahr' }
];

const WEEKDAYS = ['So.', 'Mo.', 'Di.', 'Mi.', 'Do.', 'Fr.', 'Sa.'];
const MONTHS = [
	'Jänner',
	'Februar',
	'März',
	'April',
	'Mai',
	'Juni',
	'Juli',
	'August',
	'September',
	'Oktober',
	'November',
	'Dezember'
];

/** Heute in Wiener Zeit als `JJJJ-MM-TT`. */
export function todayIso(): string {
	return todayVienna();
}

function split(day: string): [number, number, number] {
	const [year, month, date] = day.split('-').map(Number);
	return [year ?? Number.NaN, month ?? Number.NaN, date ?? Number.NaN];
}

function pad(value: number, width = 2): string {
	return String(value).padStart(width, '0');
}

function iso(year: number, month: number, date: number): string {
	return `${pad(year, 4)}-${pad(month)}-${pad(date)}`;
}

function daysInMonth(year: number, month: number): number {
	return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** Wochentag, 0 = Sonntag */
function weekday(day: string): number {
	const [year, month, date] = split(day);
	return new Date(Date.UTC(year, month - 1, date)).getUTCDay();
}

function monday(day: string): string {
	return addDays(day, -((weekday(day) + 6) % 7));
}

/** Kalenderwoche nach ISO 8601: die Woche gehört zu dem Jahr, in dem ihr Donnerstag liegt. */
function isoWeek(day: string): number {
	const thursday = addDays(monday(day), 3);
	const [year] = split(thursday);
	const start = Date.UTC(year, 0, 1);
	const [y, m, d] = split(thursday);
	const dayOfYear = Math.round((Date.UTC(y, m - 1, d) - start) / 86_400_000);
	return Math.floor(dayOfYear / 7) + 1;
}

function dayMonth(day: string): string {
	const [, month, date] = split(day);
	return `${pad(date)}.${pad(month)}.`;
}

/** Erster und letzter Tag des Zeitraums, der `day` enthält (Woche: Montag bis Sonntag). */
export function periodRange(period: Period, day: string): { first: string; last: string } {
	const [year, month] = split(day);
	switch (period) {
		case 'day':
			return { first: day, last: day };
		case 'week': {
			const first = monday(day);
			return { first, last: addDays(first, 6) };
		}
		case 'month':
			return { first: iso(year, month, 1), last: iso(year, month, daysInMonth(year, month)) };
		case 'year':
			return { first: iso(year, 1, 1), last: iso(year, 12, 31) };
	}
}

/** Überschrift: „Fr., 09.10.2026“, „KW 41 · 05.10.–11.10.2026“, „Oktober 2026“, „2026“ */
export function periodTitle(period: Period, day: string): string {
	const [year, month] = split(day);
	switch (period) {
		case 'day':
			return `${WEEKDAYS[weekday(day)]}, ${dayMonth(day)}${year}`;
		case 'week': {
			const { first, last } = periodRange('week', day);
			const [firstYear] = split(first);
			const [lastYear] = split(last);
			const from = firstYear === lastYear ? dayMonth(first) : `${dayMonth(first)}${firstYear}`;
			return `KW ${isoWeek(day)} · ${from}–${dayMonth(last)}${lastYear}`;
		}
		case 'month':
			return `${MONTHS[month - 1]} ${year}`;
		case 'year':
			return String(year);
	}
}

/** Einen Zeitraum weiter oder zurück; Monat und Jahr behalten den Tag, soweit es ihn gibt. */
export function shift(period: Period, day: string, step: -1 | 1): string {
	const [year, month, date] = split(day);
	switch (period) {
		case 'day':
			return addDays(day, step);
		case 'week':
			return addDays(day, 7 * step);
		case 'month': {
			const index = year * 12 + (month - 1) + step;
			const targetYear = Math.floor(index / 12);
			const targetMonth = (index % 12) + 1;
			return iso(targetYear, targetMonth, Math.min(date, daysInMonth(targetYear, targetMonth)));
		}
		case 'year':
			return iso(year + step, month, Math.min(date, daysInMonth(year + step, month)));
	}
}

/** Liegt `today` im Zeitraum, der `day` enthält? Dann ist er der laufende. */
export function isRunning(period: Period, day: string, today: string): boolean {
	const { first, last } = periodRange(period, day);
	return first <= today && today <= last;
}

/** `?zeitraum=…`: unbekannte Werte und fehlender Parameter geben „Tag“. */
export function parsePeriod(param: string | null): Period {
	return PERIODS.find((entry) => entry.param === param)?.id ?? 'day';
}

export function periodParam(period: Period): string {
	return PERIODS.find((entry) => entry.id === period)?.param ?? 'tag';
}

/** `?datum=…`: nur echte Kalendertage, sonst `fallback`. */
export function parseDay(param: string | null, fallback: string): string {
	if (param === null || !/^\d{4}-\d{2}-\d{2}$/.test(param)) return fallback;
	const [year, month, date] = split(param);
	return month >= 1 && month <= 12 && date >= 1 && date <= daysInMonth(year, month)
		? param
		: fallback;
}
