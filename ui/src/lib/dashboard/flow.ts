// Zweige des Energieflusses rund um den Hausanschluss (Vorzeichen: Netz + = Bezug, Akku + = Laden)
import type { Derived, Limits, TreeNode } from '$lib/api/schemas';
import { wallboxName } from '$lib/wallboxes';

/** Darunter gilt ein Zweig als ruhend (Messrauschen, Standby). */
export const IDLE_W = 20;

/** `in` = zum Hausanschluss hin; `implausible` = gegen die einzig mögliche Richtung (Messfehler) */
export type FlowDirection = 'in' | 'out' | 'idle' | 'unknown' | 'implausible';

export interface FlowBranch {
	id: string;
	label: string;
	caption: string;
	/** Betrag; die Richtung trägt das Vorzeichen (bei `implausible` der Messwert mit Vorzeichen) */
	powerW: number | null;
	direction: FlowDirection;
}

export function wallboxLabels(limits: Limits | null | undefined): Record<string, string> {
	const labels: Record<string, string> = {};
	for (const [key, wallbox] of Object.entries(limits?.wallboxes ?? {})) {
		labels[key] = wallboxName(key, wallbox);
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
	captions: { in?: string; out?: string } = {},
	oneWay = false
): FlowBranch {
	// PV erzeugt nur, Haus und Wallboxen verbrauchen nur: ein Wert gegen diese Richtung ist ein
	// Mess- oder Zeitfehler und kein Rückfluss
	if (oneWay && typeof value === 'number' && Number.isFinite(value) && value < -IDLE_W) {
		return { id, label, caption: 'unplausibel', powerW: value, direction: 'implausible' };
	}
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
		branch('pv', 'PV', derived?.pv_total_w, 'in', {}, true),
		branch('grid', 'Netz', derived?.grid_w, 'in', { in: 'Bezug', out: 'Einspeisung' }),
		branch('battery', 'Akku', derived?.battery_w, 'out', { in: 'entlädt', out: 'lädt' }),
		branch('house', 'Haus', derived?.house_load_w, 'out', {}, true),
		...wallboxKeys
			.sort()
			.map((key) =>
				branch(`wallbox:${key}`, wallboxes[key] ?? key, derived?.wallbox_w[key], 'out', {}, true)
			)
	];
}

/** Verbraucher unter dem Haus im Energiefluss */
export interface FlowConsumer {
	id: string;
	name: string;
	color: string | null;
	powerW: number | null;
}

/** mehr passen nebeneinander nicht lesbar ins Diagramm */
export const MAX_FLOW_CONSUMERS = 6;

/**
 * Verbraucher der ersten Ebene und „Sonstiges“. Bei zu vielen bleiben die größten; der Rest wird
 * zu „n weitere“ zusammengefasst (ohne Wert, wenn einer davon keinen hat).
 */
export function flowConsumers(tree: TreeNode | null | undefined): FlowConsumer[] {
	if (!tree || tree.children.length === 0) return [];
	const items: FlowConsumer[] = tree.children.map((child) => ({
		id: `consumer:${child.id}`,
		name: child.name,
		color: child.color,
		powerW: child.power_w
	}));
	const other: FlowConsumer = { id: 'other', name: 'Sonstiges', color: null, powerW: tree.other_w };
	if (items.length < MAX_FLOW_CONSUMERS) return [...items, other];
	const largest = [...items]
		.sort((a, b) => (b.powerW ?? -Infinity) - (a.powerW ?? -Infinity))
		.slice(0, MAX_FLOW_CONSUMERS - 2);
	const rest = items.filter((item) => !largest.includes(item));
	const restW = rest.every((item) => item.powerW !== null)
		? rest.reduce((sum, item) => sum + (item.powerW ?? 0), 0)
		: null;
	return [
		...items.filter((item) => largest.includes(item)),
		{ id: 'more', name: `${rest.length} weitere`, color: null, powerW: restW },
		other
	];
}
