import { expect, it } from 'vitest';
import { presetDays, rangeQuery } from './ranges';

it('Heute am 25.10. umfasst 25 h', () => {
	const days = presetDays('heute', new Date('2026-10-25T10:00:00Z'));
	expect(days).toEqual({ first: '2026-10-25', last: '2026-10-25' });
	expect(rangeQuery(days)).toEqual({
		from: '2026-10-24T22:00:00.000Z',
		to: '2026-10-25T23:00:00.000Z'
	});
});

it('rechnet Gestern, 7 und 30 Tage in Wien', () => {
	const lateEvening = new Date('2026-10-09T22:30:00Z'); // 10.10., 00:30 in Wien
	expect(presetDays('gestern', lateEvening)).toEqual({ first: '2026-10-09', last: '2026-10-09' });
	expect(presetDays('7t', lateEvening)).toEqual({ first: '2026-10-04', last: '2026-10-10' });
	expect(presetDays('30t', lateEvening)).toEqual({ first: '2026-09-11', last: '2026-10-10' });
});
