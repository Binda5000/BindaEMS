import { describe, expect, it } from 'vitest';
import type { Palette } from '$lib/charts/palette';
import { limits } from '$lib/testing/fixtures';
import { parseVehicle, statsHref, vehicleChoices, vehicleColor } from './vehicles';

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	load: '#80f',
	series: ['#00f', '#0a0', '#a0a']
};

describe('Auswahl des Autos', () => {
	it('bietet Alle, die Autos der Konfiguration, Gast und Unbekannt', () => {
		const choices = vehicleChoices(limits(), null);
		expect(choices.map((c) => c.label)).toEqual([
			'Alle',
			'Tesla Model 3',
			'e-Golf',
			'Gast',
			'Unbekannt'
		]);
		expect(choices.map((c) => c.key)).toEqual([null, 'tesla', 'egolf', 'guest', 'unknown']);
	});

	it('zeigt einen unbekannten Schlüssel aus der Adresse als eigenen Knopf', () => {
		expect(vehicleChoices(limits(), 'old-car').at(-1)).toEqual({
			key: 'old-car',
			label: 'old-car'
		});
		expect(vehicleChoices(undefined, null).map((c) => c.key)).toEqual([null, 'guest', 'unknown']);
	});

	it('hält die Auswahl in der Adresse', () => {
		expect(statsHref('month', '2026-10-09', null)).toBe(
			'/statistik?zeitraum=monat&datum=2026-10-09'
		);
		expect(statsHref('year', '2026-10-09', 'tesla')).toBe(
			'/statistik?zeitraum=jahr&datum=2026-10-09&auto=tesla'
		);
		expect(statsHref('day', '2026-10-09', 'a b&c')).toBe(
			'/statistik?zeitraum=tag&datum=2026-10-09&auto=a%20b%26c'
		);
		expect(parseVehicle(null)).toBeNull();
		expect(parseVehicle('')).toBeNull();
		expect(parseVehicle('egolf')).toBe('egolf');
	});
});

describe('Farben der Autos', () => {
	it('nimmt die Farbe nach der Stelle in der Konfiguration, Gast zuerst, sonst gedämpft', () => {
		expect(vehicleColor('tesla', limits(), palette)).toBe('#0a0');
		expect(vehicleColor('egolf', limits(), palette)).toBe('#a0a');
		expect(vehicleColor('guest', limits(), palette)).toBe('#00f');
		expect(vehicleColor('unknown', limits(), palette)).toBe('#666');
		expect(vehicleColor('tesla', undefined, palette)).toBe('#666');
	});
});
