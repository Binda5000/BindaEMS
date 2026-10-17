import { expect, it } from 'vitest';
import { chargingSession, daySummary, stats } from './fixtures';

it('liefert die Statistik des Monats aus der Demo-Welt, Felder überschreibbar', () => {
	const base = stats();
	expect(base.period).toBe('month');
	expect(base.vehicles.length).toBeGreaterThan(0);
	expect(stats({ coverage: 0.5 }).coverage).toBe(0.5);
	expect(stats({ coverage: 0.5 }).vehicles).toEqual(base.vehicles);
});

it('liefert eine beendete Ladesession, Felder überschreibbar', () => {
	const base = chargingSession();
	expect(base.open).toBe(false);
	expect(base.end).not.toBeNull();
	const open = chargingSession({ open: true, end: null });
	expect(open.end).toBeNull();
	expect(open.wallbox).toBe(base.wallbox);
});

it('liefert für den Tag der Abrechnung den 09.10., nicht den davor', () => {
	expect(daySummary().date).toBe('2026-10-09');
});
