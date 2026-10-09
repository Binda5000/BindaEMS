import { describe, expect, it } from 'vitest';
import { addDays, localDayRange, localRange, parseIso, todayVienna } from './time';

describe('lokale Tage', () => {
	it('dauern an normalen Tagen 24 h', () => {
		expect(localDayRange('2026-10-09')).toEqual({
			from: '2026-10-08T22:00:00.000Z',
			to: '2026-10-09T22:00:00.000Z'
		});
	});

	it('dauern beim Ende der Sommerzeit 25 h', () => {
		expect(localDayRange('2026-10-25')).toEqual({
			from: '2026-10-24T22:00:00.000Z',
			to: '2026-10-25T23:00:00.000Z'
		});
	});

	it('dauern beim Beginn der Sommerzeit 23 h', () => {
		expect(localDayRange('2026-03-29')).toEqual({
			from: '2026-03-28T23:00:00.000Z',
			to: '2026-03-29T22:00:00.000Z'
		});
	});

	it('reichen über mehrere Tage', () => {
		expect(localRange('2026-10-24', '2026-10-25')).toEqual({
			from: '2026-10-23T22:00:00.000Z',
			to: '2026-10-25T23:00:00.000Z'
		});
	});
});

it('bestimmt „heute“ in Wien, nicht in UTC', () => {
	expect(todayVienna(new Date('2026-10-09T22:30:00Z'))).toBe('2026-10-10');
	expect(addDays('2026-10-31', 1)).toBe('2026-11-01');
	expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
});

it('liest Zeitstempel mit Mikrosekunden (Safari kennt nur Millisekunden)', () => {
	expect(parseIso('2026-10-09T08:00:00.123456+00:00')).toBe(Date.UTC(2026, 9, 9, 8, 0, 0, 123));
	expect(parseIso('2026-10-09T08:00:00+00:00')).toBe(Date.UTC(2026, 9, 9, 8));
	expect(parseIso('kein Datum')).toBeNull();
});
