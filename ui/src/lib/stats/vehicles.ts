// Auswahl des Autos auf der Statistik: Knöpfe, Adresse und Farben
import type { Limits } from '$lib/api/schemas';
import type { Palette } from '$lib/charts/palette';
import { periodParam, type Period } from './period';
import { vehicleLabel } from './view';

export interface VehicleChoice {
	/** `null`: alle Autos */
	key: string | null;
	label: string;
}

/**
 * „Alle“, die Autos der Konfiguration in ihrer Reihenfolge, „Gast“ und „Unbekannt“. Ein
 * gewählter Schlüssel, den es nicht (mehr) gibt, steht zuletzt mit dem Schlüssel als Namen.
 */
export function vehicleChoices(
	limits: Limits | undefined,
	selected: string | null
): VehicleChoice[] {
	const choices: VehicleChoice[] = [
		{ key: null, label: 'Alle' },
		...Object.entries(limits?.vehicles ?? {}).map(([key, vehicle]) => ({
			key,
			label: vehicleLabel(key, vehicle.name)
		})),
		{ key: 'guest', label: vehicleLabel('guest', '') },
		{ key: 'unknown', label: vehicleLabel('unknown', '') }
	];
	if (selected !== null && !choices.some((choice) => choice.key === selected)) {
		choices.push({ key: selected, label: selected });
	}
	return choices;
}

/** `?auto=…`: leer oder fehlend gibt „alle“ */
export function parseVehicle(param: string | null): string | null {
	return param === null || param === '' ? null : param;
}

/** Adresse der Statistik mit Zeitraum, Datum und (falls gewählt) Auto */
export function statsHref(period: Period, day: string, vehicle: string | null): string {
	const base = `/statistik?zeitraum=${periodParam(period)}&datum=${day}`;
	return vehicle === null ? base : `${base}&auto=${encodeURIComponent(vehicle)}`;
}

/** Farbe eines Autos: Nr. i der Konfiguration die Reihe i + 1 der Palette, Gast die erste */
export function vehicleColor(key: string, limits: Limits | undefined, palette: Palette): string {
	const { series } = palette;
	if (key === 'guest') return series[0] ?? palette.muted;
	const index = Object.keys(limits?.vehicles ?? {}).indexOf(key);
	if (index < 0) return palette.muted;
	return series[(index + 1) % series.length] ?? palette.muted;
}
