// Gemeinsames der Übersicht: Ruheschwelle, Namen der Wallboxen und die Verbraucher im Haus
import type { Limits, TreeNode } from '$lib/api/schemas';
import { wallboxName } from '$lib/wallboxes';

/** Darunter gilt ein Zweig als ruhend (Messrauschen, Standby). */
export const IDLE_W = 20;

export function wallboxLabels(limits: Limits | null | undefined): Record<string, string> {
	const labels: Record<string, string> = {};
	for (const [key, wallbox] of Object.entries(limits?.wallboxes ?? {})) {
		labels[key] = wallboxName(key, wallbox);
	}
	return labels;
}

/** Verbraucher der ersten Ebene für „Im Haus“ auf der Übersicht */
export interface FlowConsumer {
	id: string;
	name: string;
	color: string | null;
	powerW: number | null;
}

/** mehr bleiben in der Zeile „Im Haus“ nicht lesbar */
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
