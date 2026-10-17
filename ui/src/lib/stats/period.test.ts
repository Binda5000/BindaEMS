import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	isRunning,
	parseDay,
	parsePeriod,
	periodParam,
	periodRange,
	periodTitle,
	shift,
	todayIso
} from './period';

afterEach(() => {
	vi.useRealTimers();
});

describe('Überschrift', () => {
	it('titles each period', () => {
		expect(periodTitle('day', '2026-10-09')).toBe('Fr., 09.10.2026');
		expect(periodTitle('week', '2026-10-09')).toBe('KW 41 · 05.10.–11.10.2026');
		expect(periodTitle('month', '2026-10-09')).toBe('Oktober 2026');
		expect(periodTitle('year', '2026-10-09')).toBe('2026');
	});

	it('nennt alle Wochentage und Monate auf Deutsch (Österreich)', () => {
		expect(
			['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-10', '2026-10-11'].map(
				(day) => periodTitle('day', day).slice(0, 3)
			)
		).toEqual(['Mo.', 'Di.', 'Mi.', 'Do.', 'Sa.', 'So.']);
		expect(periodTitle('month', '2027-01-15')).toBe('Jänner 2027');
		expect(periodTitle('month', '2026-03-01')).toBe('März 2026');
	});

	it('zählt Kalenderwochen nach ISO, auch über den Jahreswechsel', () => {
		// 31.12.2026 ist ein Donnerstag: die Woche gehört mit 53 Wochen zu 2026
		expect(periodTitle('week', '2026-12-31')).toBe('KW 53 · 28.12.2026–03.01.2027');
		expect(periodTitle('week', '2027-01-04')).toBe('KW 1 · 04.01.–10.01.2027');
		// 1.1.2027 liegt noch in KW 53 des Vorjahres
		expect(periodTitle('week', '2027-01-01')).toBe('KW 53 · 28.12.2026–03.01.2027');
	});

	it('beginnt die Woche am Montag, auch wenn der Sonntag gewählt ist', () => {
		expect(periodTitle('week', '2026-10-11')).toBe('KW 41 · 05.10.–11.10.2026');
		expect(periodTitle('week', '2026-10-05')).toBe('KW 41 · 05.10.–11.10.2026');
	});
});

describe('Blättern', () => {
	it('shifts a month from 31 January to the end of February', () => {
		expect(shift('month', '2026-01-31', 1)).toBe('2026-02-28');
		expect(shift('month', '2028-03-31', -1)).toBe('2028-02-29');
	});

	it('blättert Tage und Wochen über Monats- und Zeitumstellungsgrenzen', () => {
		expect(shift('day', '2026-10-31', 1)).toBe('2026-11-01');
		expect(shift('day', '2026-03-29', -1)).toBe('2026-03-28');
		expect(shift('week', '2026-10-09', -1)).toBe('2026-10-02');
		expect(shift('week', '2026-12-30', 1)).toBe('2027-01-06');
	});

	it('blättert Monate über den Jahreswechsel und Jahre mit Schalttag', () => {
		expect(shift('month', '2026-12-15', 1)).toBe('2027-01-15');
		expect(shift('month', '2026-01-15', -1)).toBe('2025-12-15');
		expect(shift('year', '2026-10-09', -1)).toBe('2025-10-09');
		expect(shift('year', '2028-02-29', 1)).toBe('2029-02-28');
	});
});

describe('Zeitraum', () => {
	it('gibt ersten und letzten Tag an', () => {
		expect(periodRange('day', '2026-10-09')).toEqual({ first: '2026-10-09', last: '2026-10-09' });
		expect(periodRange('week', '2026-10-09')).toEqual({ first: '2026-10-05', last: '2026-10-11' });
		expect(periodRange('month', '2026-02-10')).toEqual({ first: '2026-02-01', last: '2026-02-28' });
		expect(periodRange('month', '2028-02-10')).toEqual({ first: '2028-02-01', last: '2028-02-29' });
		expect(periodRange('year', '2026-10-09')).toEqual({ first: '2026-01-01', last: '2026-12-31' });
	});

	it('erkennt den laufenden Zeitraum', () => {
		expect(isRunning('week', '2026-10-05', '2026-10-09')).toBe(true);
		expect(isRunning('week', '2026-10-02', '2026-10-09')).toBe(false);
		expect(isRunning('month', '2026-10-01', '2026-10-09')).toBe(true);
		expect(isRunning('day', '2026-10-10', '2026-10-09')).toBe(false);
	});

	it('rechnet „heute“ in Wiener Zeit', () => {
		vi.useFakeTimers({ toFake: ['Date'] });
		vi.setSystemTime(new Date('2026-10-09T21:59:00Z')); // 23:59 in Wien
		expect(todayIso()).toBe('2026-10-09');
		vi.setSystemTime(new Date('2026-10-09T22:01:00Z')); // 00:01 am 10.10.
		expect(todayIso()).toBe('2026-10-10');
	});
});

describe('URL-Parameter', () => {
	it('übersetzt den Zeitraum und fällt auf „Tag“ zurück', () => {
		expect(parsePeriod('woche')).toBe('week');
		expect(parsePeriod('monat')).toBe('month');
		expect(parsePeriod('jahr')).toBe('year');
		expect(parsePeriod('tag')).toBe('day');
		expect(parsePeriod(null)).toBe('day');
		expect(parsePeriod('quartal')).toBe('day');
		expect(periodParam('week')).toBe('woche');
	});

	it('liest nur echte Datumsangaben', () => {
		expect(parseDay('2026-10-09', '2026-10-17')).toBe('2026-10-09');
		expect(parseDay('2026-02-30', '2026-10-17')).toBe('2026-10-17');
		expect(parseDay('09.10.2026', '2026-10-17')).toBe('2026-10-17');
		expect(parseDay(null, '2026-10-17')).toBe('2026-10-17');
	});
});
