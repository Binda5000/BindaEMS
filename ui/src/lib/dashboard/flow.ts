// Zweige des Energieflusses rund um den Hausanschluss (Vorzeichen: Netz + = Bezug, Akku + = Laden)
import type { Derived, Limits } from '$lib/api/schemas';

/** Darunter gilt ein Zweig als ruhend (Messrauschen, Standby). */
export const IDLE_W = 20;

/** `in` = zum Hausanschluss hin */
export type FlowDirection = 'in' | 'out' | 'idle' | 'unknown';

export interface FlowBranch {
	id: string;
	label: string;
	caption: string;
	/** Betrag; die Richtung trägt das Vorzeichen */
	powerW: number | null;
	direction: FlowDirection;
}

const WALLBOX_TYPES: Record<string, string> = {
	victron_evcs_ns: 'EVCS',
	tesla_wall_connector_gen3: 'Wall Connector'
};

export function wallboxLabels(limits: Limits | null | undefined): Record<string, string> {
	const labels: Record<string, string> = {};
	for (const [key, wallbox] of Object.entries(limits?.wallboxes ?? {})) {
		labels[key] = WALLBOX_TYPES[wallbox.type] ?? key;
	}
	return labels;
}

/** Richtung, wenn ein positiver Wert zum Hausanschluss hin fließt. */
function direction(value: number | null | undefined, positive: 'in' | 'out'): FlowDirection {
	if (value === null || value === undefined || !Number.isFinite(value)) return 'unknown';
	if (Math.abs(value) <= IDLE_W) return 'idle';
	if (value > 0) return positive;
	return positive === 'in' ? 'out' : 'in';
}

function branch(
	id: string,
	label: string,
	value: number | null | undefined,
	positive: 'in' | 'out',
	captions: { in?: string; out?: string } = {}
): FlowBranch {
	const dir = direction(value, positive);
	const known = dir !== 'unknown' && value !== null && value !== undefined;
	const caption = (dir === 'in' || dir === 'out' ? captions[dir] : undefined) ?? label;
	return { id, label, caption, powerW: known ? Math.abs(value) : null, direction: dir };
}

export function flowBranches(
	derived: Derived | null | undefined,
	wallboxes: Record<string, string>
): FlowBranch[] {
	const wallboxKeys = [
		...new Set([...Object.keys(wallboxes), ...Object.keys(derived?.wallbox_w ?? {})])
	];
	return [
		branch('pv', 'PV', derived?.pv_total_w, 'in'),
		branch('grid', 'Netz', derived?.grid_w, 'in', { in: 'Bezug', out: 'Einspeisung' }),
		branch('battery', 'Akku', derived?.battery_w, 'out', { in: 'entlädt', out: 'lädt' }),
		branch('house', 'Haus', derived?.house_load_w, 'out'),
		...wallboxKeys
			.sort()
			.map((key) => branch(`wallbox:${key}`, wallboxes[key] ?? key, derived?.wallbox_w[key], 'out'))
	];
}
