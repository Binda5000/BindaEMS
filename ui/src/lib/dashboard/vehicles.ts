// Fahrzeug-Karten der Übersicht: Ladestand, Wallbox-Status und Modus, dazu eine Karte je Gast
import type { CoreState, Limits, VehicleHealth, WallboxHealth } from '$lib/api/schemas';
import { modeLabel } from '$lib/charging/view';
import { formatPower } from '$lib/format';
import { IDLE_W } from './flow';
import { socReading, type SocQuality } from './socs';

export interface VehicleCard {
	id: string;
	name: string;
	socPct: number | null;
	socQuality: SocQuality;
	guest: boolean;
	status: string;
	/** Anzeigename des Modus; `null`, solange er unbekannt ist */
	mode: string | null;
}

function power(state: CoreState | null | undefined, wallbox: string): number | null {
	const watts = state?.derived.wallbox_w[wallbox];
	return typeof watts === 'number' && Number.isFinite(watts) ? watts : null;
}

function charging(label: string, watts: number | null): string {
	return watts === null ? `${label} · lädt` : `${label} · lädt ${formatPower(watts)}`;
}

function vehicleStatus(
	health: VehicleHealth | undefined,
	state: CoreState | null | undefined,
	wallboxes: Record<string, string>
): string {
	const wallbox = health?.wallbox;
	if (!wallbox) return 'nicht angesteckt';
	const label = wallboxes[wallbox] ?? wallbox;
	if (health.charging === true) return charging(label, power(state, wallbox));
	return health.plugged === true ? `${label} · angesteckt` : 'nicht angesteckt';
}

function guestStatus(
	wallbox: WallboxHealth,
	state: CoreState | null | undefined,
	wallboxes: Record<string, string>
): string {
	const label = wallboxes[wallbox.id] ?? wallbox.id;
	const watts = power(state, wallbox.id);
	return watts !== null && watts > IDLE_W ? charging(label, watts) : `${label} · angesteckt`;
}

/** Die Fahrzeuge der Einstellungen in deren Reihenfolge, danach je Wallbox mit Gast eine Karte */
export function vehicleCards(
	state: CoreState | null | undefined,
	limits: Limits | null | undefined,
	health: { vehicles: VehicleHealth[]; wallboxes: WallboxHealth[] } | null | undefined,
	wallboxes: Record<string, string>
): VehicleCard[] {
	const cards: VehicleCard[] = Object.entries(limits?.vehicles ?? {}).map(([key, vehicle]) => {
		const own = health?.vehicles.find((item) => item.id === key);
		const soc = socReading(state, `vehicle.${key}.soc_pct`);
		return {
			id: key,
			name: vehicle.name,
			socPct: soc.pct,
			socQuality: soc.quality,
			guest: false,
			status: vehicleStatus(own, state, wallboxes),
			mode: own?.mode ? modeLabel(own.mode) : null
		};
	});
	for (const wallbox of health?.wallboxes ?? []) {
		if (wallbox.occupant !== 'guest') continue;
		cards.push({
			id: `guest:${wallbox.id}`,
			name: 'Gast',
			socPct: null,
			socQuality: 'missing',
			guest: true,
			status: guestStatus(wallbox, state, wallboxes),
			mode: null
		});
	}
	return cards;
}
