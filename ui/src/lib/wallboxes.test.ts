import { expect, it } from 'vitest';
import { actuatorDisplay, seriesLabel, wallboxDisplay } from './wallboxes';

const names = { evcs: 'EVCS', twc: 'Garage' };

it('wallboxDisplay nimmt den Namen aus den Einstellungen, sonst den Schlüssel', () => {
	expect(wallboxDisplay('twc', names)).toBe('Garage');
	expect(wallboxDisplay('neu', names)).toBe('neu');
});

it('actuatorDisplay ersetzt nur die Bezeichnung einer Wallbox', () => {
	expect(actuatorDisplay('wallbox.evcs', 'EVCS „evcs“', names)).toBe('EVCS');
	expect(actuatorDisplay('wallbox.neu', 'Neu „neu“', names)).toBe('neu');
	expect(actuatorDisplay('victron', 'Victron (ESS)', names)).toBe('Victron (ESS)');
	expect(actuatorDisplay('vehicle.tesla', 'Tesla Model 3', names)).toBe('Tesla Model 3');
});

it('seriesLabel nennt die Wallbox-Reihe beim Namen, andere Reihen und unbekannte bleiben', () => {
	expect(seriesLabel('wallbox.evcs', 'Wallbox evcs', names)).toBe('EVCS');
	expect(seriesLabel('wallbox.neu', 'Wallbox neu', names)).toBe('Wallbox neu');
	expect(seriesLabel('grid', 'Netz', names)).toBe('Netz');
});
