import { expect, it } from 'vitest';
import { durationText, hoursText, shortDate } from './backup';

it('hoursText: unter 10 h eine Nachkommastelle, sonst ganze Stunden, mit geschütztem Leerzeichen', () => {
	expect(hoursText(7.46)).toBe('≈ 7,5 h');
	expect(hoursText(31.4)).toBe('≈ 31 h');
	expect(hoursText(null)).toBeNull();
});

it('hoursText: Grenzfälle kaufmännisch gerundet, wie hours_text im Server', () => {
	expect(hoursText(0.04)).toBe('≈ 0 h');
	expect(hoursText(9.96)).toBe('≈ 10 h');
	expect(hoursText(10.5)).toBe('≈ 11 h');
	expect(hoursText(12.5)).toBe('≈ 13 h');
});

it('durationText: Sekunden, Minuten, Stunden', () => {
	expect(durationText(30)).toBe('30 s');
	expect(durationText(840)).toBe('14 min');
	expect(durationText(7500)).toBe('2 h 5 min');
});

it('shortDate: Tag und Monat ohne führende Null', () => {
	expect(shortDate('2026-11-03')).toBe('3.11.');
	expect(shortDate('2026-10-28')).toBe('28.10.');
});
