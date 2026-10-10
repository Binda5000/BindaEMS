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
