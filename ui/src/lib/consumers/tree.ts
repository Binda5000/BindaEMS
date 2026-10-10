// Verbraucherbaum als flache Zeilen; „Sonstiges“ folgt den Kindern jedes Knotens mit Kindern
import type { TreeNode } from '$lib/api/schemas';

export interface ConsumerRow {
	key: string;
	id: number | null;
	name: string;
	depth: number;
	powerW: number | null;
	/** warum `powerW` fehlt */
	note: string | null;
	color: string | null;
	kind: 'root' | 'consumer' | 'other';
	mismatch: boolean;
}

export function consumerRows(tree: TreeNode): ConsumerRow[] {
	const rows: ConsumerRow[] = [];
	const visit = (node: TreeNode, depth: number, key: string) => {
		rows.push({
			key,
			id: node.id,
			name: node.name,
			depth,
			powerW: node.power_w,
			note: node.note,
			color: node.color,
			kind: depth === 0 ? 'root' : 'consumer',
			mismatch: node.mismatch
		});
		for (const child of node.children) visit(child, depth + 1, `consumer:${child.id}`);
		if (node.children.length > 0) {
			rows.push({
				key: `${key}:other`,
				id: null,
				name: 'Sonstiges',
				depth: depth + 1,
				powerW: node.other_w,
				note: null,
				color: null,
				kind: 'other',
				mismatch: node.mismatch
			});
		}
	};
	visit(tree, 0, 'root');
	return rows;
}
