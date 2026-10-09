// Zeiträume des Verlaufs als lokale Tage in Wien (inklusiv) und ihre UTC-Grenzen
import { addDays, localRange, todayVienna } from '$lib/time';

export type RangePreset = 'heute' | 'gestern' | '7t' | '30t';

export const PRESETS: { id: RangePreset; label: string }[] = [
	{ id: 'heute', label: 'Heute' },
	{ id: 'gestern', label: 'Gestern' },
	{ id: '7t', label: '7 Tage' },
	{ id: '30t', label: '30 Tage' }
];

export interface DayRange {
	first: string;
	last: string;
}

export const DEFAULT_SERIES = ['grid', 'pv', 'battery', 'house'] as const;
export const MAX_SERIES = 8;
export const MAX_DAYS = 400;

export function presetDays(preset: RangePreset, now: Date): DayRange {
	const today = todayVienna(now);
	switch (preset) {
		case 'heute':
			return { first: today, last: today };
		case 'gestern': {
			const yesterday = addDays(today, -1);
			return { first: yesterday, last: yesterday };
		}
		case '7t':
			return { first: addDays(today, -6), last: today };
		case '30t':
			return { first: addDays(today, -29), last: today };
	}
}

export function rangeQuery(days: DayRange): { from: string; to: string } {
	return localRange(days.first, days.last);
}

/** Enthält der Zeitraum den heutigen Tag? Dann laden Diagramm und Bilanz im Takt nach. */
export function isToday(days: DayRange, now: Date): boolean {
	const today = todayVienna(now);
	return days.first <= today && today <= days.last;
}

/** Anzahl der Tage von `first` bis `last` (inklusiv). */
export function dayCount(days: DayRange): number {
	const start = Date.parse(`${days.first}T00:00:00Z`);
	const end = Date.parse(`${days.last}T00:00:00Z`);
	return Math.round((end - start) / 86_400_000) + 1;
}
