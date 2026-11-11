import { expect, it } from 'vitest';
import type { ForecastSnow } from '$lib/api/schemas';
import { isCut, snowText } from './snow';

const now = new Date('2026-10-09T08:00:00Z'); // 10:00 in Wien

const snow = (overrides: Partial<ForecastSnow> = {}): ForecastSnow => ({
	state: 'vermutet',
	factor_pct: 10,
	since: '2026-10-09T05:15:00+00:00',
	override: null,
	...overrides
});

it('nennt Zustand, Anteil der Prognose und „seit“ in Wiener Zeit', () => {
	expect(snowText(snow(), now)).toBe('vermutet, 10 % der Prognose, seit 07:15');
	expect(snowText(snow({ state: 'gemessen', factor_pct: 35 }), now)).toBe(
		'gemessen, 35 % der Prognose, seit 07:15'
	);
});

it('nennt den Tag, wenn „seit“ vor heute liegt, und lässt es ohne Zeitpunkt weg', () => {
	expect(snowText(snow({ since: '2026-10-08T21:30:00+00:00' }), now)).toBe(
		'vermutet, 10 % der Prognose, seit 08.10. 23:30'
	);
	expect(snowText(snow({ since: null }), now)).toBe('vermutet, 10 % der Prognose');
});

it('frei ist nur „frei“', () => {
	expect(snowText(snow({ state: 'frei', factor_pct: 100, since: null }), now)).toBe('frei');
});

it('erkennt gekürzte Slots', () => {
	const slot = (p50: number, uncut: number) => ({
		start: '2026-10-09T08:00:00+00:00',
		p50_w: p50,
		p25_w: p50,
		p75_w: p50,
		uncut_p50_w: uncut
	});
	expect(isCut([slot(1000, 1000), slot(0, 0)])).toBe(false);
	expect(isCut([slot(1000, 1000), slot(100, 1000)])).toBe(true);
});
