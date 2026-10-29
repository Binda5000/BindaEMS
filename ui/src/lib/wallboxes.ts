// Anzeigename einer Ladestation: eigener Name aus den Einstellungen, sonst der Typ
import type { Limits } from '$lib/api/schemas';

type WallboxLimits = Limits['wallboxes'][string];

export const WALLBOX_TYPES: Record<WallboxLimits['type'], string> = {
	victron_evcs_ns: 'EVCS',
	tesla_wall_connector_gen3: 'Wall Connector'
};

/** höchstens so viele Zeichen (wie der Server) */
export const WALLBOX_NAME_MAX = 40;

export function wallboxName(key: string, wallbox: WallboxLimits | undefined): string {
	return wallbox?.name ?? (wallbox ? WALLBOX_TYPES[wallbox.type] : key);
}

/** Der Name einer Wallbox aus `names` (Schlüssel → Anzeigename), sonst der Schlüssel */
export function wallboxDisplay(key: string, names: Record<string, string>): string {
	return names[key] ?? key;
}

/** `wallbox.<key>`: Aktor-ID und Reihen-ID im Verlauf */
const WALLBOX_ID = /^wallbox\.([^.]+)$/;

/**
 * Anzeigename eines Aktors: für `wallbox.<key>` der Name der Wallbox (solange die Namen noch
 * fehlen, die Bezeichnung des core), sonst die Bezeichnung des core
 */
export function actuatorDisplay(id: string, label: string, names: Record<string, string>): string {
	const key = WALLBOX_ID.exec(id)?.[1];
	return key === undefined || !(key in names) ? label : wallboxDisplay(key, names);
}

/** Bezeichnung einer Reihe im Verlauf: `wallbox.<key>` mit dem Namen der Wallbox, sofern bekannt */
export function seriesLabel(id: string, label: string, names: Record<string, string>): string {
	const key = WALLBOX_ID.exec(id)?.[1];
	return key !== undefined && key in names ? wallboxDisplay(key, names) : label;
}
