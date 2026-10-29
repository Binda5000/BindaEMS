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

/**
 * Anzeigenamen mehrerer Aktoren auf einmal (Aktor-ID → Name). Kommt ein Name mehr als einmal vor
 * (Namen sind nicht eindeutig), zeigen diese Aktoren die Bezeichnung des core mit dem Schlüssel,
 * damit zwei Einträge nie gleich heißen.
 */
export function uniqueDisplays(
	entries: readonly { id: string; label: string }[],
	names: Record<string, string>
): Record<string, string> {
	const shown = entries.map((entry) => actuatorDisplay(entry.id, entry.label, names));
	return Object.fromEntries(
		entries.map((entry, index) => [
			entry.id,
			shown.filter((name) => name === shown[index]).length > 1 ? entry.label : shown[index]
		])
	);
}

/** Bezeichnung einer Reihe im Verlauf: `wallbox.<key>` mit dem Namen der Wallbox, sofern bekannt */
export function seriesLabel(id: string, label: string, names: Record<string, string>): string {
	return actuatorDisplay(id, label, names);
}
