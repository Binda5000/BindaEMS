import { expect, it } from 'vitest';
import { coreState, limits, reading } from '$lib/testing/fixtures';
import { socEntries } from './socs';

function withSignals(signals: Record<string, ReturnType<typeof reading>>) {
	return { ...coreState(), signals };
}

it('Ladestände tragen ihre Qualität', () => {
	const state = withSignals({
		'battery.soc_pct': reading(55),
		'vehicle.tesla.soc_pct': reading(70, 'stale')
	});
	expect(socEntries(state, limits())).toEqual([
		{ id: 'battery', label: 'Akku', pct: 55, quality: 'ok' },
		{ id: 'tesla', label: 'Tesla Model 3', pct: 70, quality: 'stale' },
		{ id: 'egolf', label: 'e-Golf', pct: null, quality: 'missing' }
	]);
});

it('ungültige Werte gelten als fehlend', () => {
	const [battery] = socEntries(
		withSignals({ 'battery.soc_pct': reading(55, 'invalid') }),
		limits()
	);
	expect(battery).toEqual({ id: 'battery', label: 'Akku', pct: null, quality: 'missing' });
});

it('ohne Zustand fehlt alles', () => {
	expect(socEntries(null, limits()).every((entry) => entry.quality === 'missing')).toBe(true);
});
