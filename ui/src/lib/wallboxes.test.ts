import { expect, it } from 'vitest';
import { actuatorDisplay, seriesLabel, uniqueDisplays, wallboxDisplay } from './wallboxes';

const names = { evcs: 'EVCS', twc: 'Garage' };

it('wallboxDisplay nimmt den Namen aus den Einstellungen, sonst den Schlüssel', () => {
	expect(wallboxDisplay('twc', names)).toBe('Garage');
	expect(wallboxDisplay('neu', names)).toBe('neu');
});

it('actuatorDisplay ersetzt nur die Bezeichnung einer bekannten Wallbox', () => {
	expect(actuatorDisplay('wallbox.evcs', 'EVCS „evcs“', names)).toBe('EVCS');
	expect(actuatorDisplay('wallbox.neu', 'Neu „neu“', names)).toBe('Neu „neu“');
	expect(actuatorDisplay('wallbox.evcs', 'EVCS „evcs“', {})).toBe('EVCS „evcs“');
	expect(actuatorDisplay('victron', 'Victron (ESS)', names)).toBe('Victron (ESS)');
	expect(actuatorDisplay('vehicle.tesla', 'Tesla Model 3', names)).toBe('Tesla Model 3');
});

it('seriesLabel nennt die Wallbox-Reihe beim Namen, andere Reihen und unbekannte bleiben', () => {
	expect(seriesLabel('wallbox.evcs', 'Wallbox evcs', names)).toBe('EVCS');
	expect(seriesLabel('wallbox.neu', 'Wallbox neu', names)).toBe('Wallbox neu');
	expect(seriesLabel('grid', 'Netz', names)).toBe('Netz');
});

it('uniqueDisplays zeigt bei gleichem Namen die Bezeichnung des core, sonst den Namen', () => {
	const entries = [
		{ id: 'victron', label: 'Victron (ESS)' },
		{ id: 'wallbox.evcs', label: 'EVCS „evcs“' },
		{ id: 'wallbox.evcs2', label: 'EVCS „evcs2“' },
		{ id: 'wallbox.twc', label: 'Wall Connector „twc“' }
	];
	expect(uniqueDisplays(entries, { evcs: 'EVCS', evcs2: 'EVCS', twc: 'Garage' })).toEqual({
		victron: 'Victron (ESS)',
		'wallbox.evcs': 'EVCS „evcs“',
		'wallbox.evcs2': 'EVCS „evcs2“',
		'wallbox.twc': 'Garage'
	});
	expect(uniqueDisplays(entries.slice(0, 2), { evcs: 'EVCS' })['wallbox.evcs']).toBe('EVCS');
});

it('uniqueDisplays unterscheidet auch einen Namen, der wie eine andere Bezeichnung lautet', () => {
	const entries = [
		{ id: 'wallbox.a', label: 'A „a“' },
		{ id: 'vehicle.x', label: 'Garage' }
	];
	expect(uniqueDisplays(entries, { a: 'Garage' })).toEqual({
		'wallbox.a': 'A „a“',
		'vehicle.x': 'Garage'
	});
});
