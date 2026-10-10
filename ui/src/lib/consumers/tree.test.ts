import { expect, it } from 'vitest';
import type { TreeNode } from '$lib/api/schemas';
import { consumerRows } from './tree';

const node = (
	id: number,
	name: string,
	power: number | null,
	children: TreeNode[] = [],
	other: number | null = null,
	mismatch = false
): TreeNode => ({
	id,
	name,
	color: '#4f8cff',
	power_w: power,
	note: null,
	other_w: other,
	mismatch,
	children
});

const tree: TreeNode = {
	...node(
		0,
		'Haus',
		1800,
		[node(3, 'Küche', 120), node(1, 'Obergeschoss', 600, [node(2, 'Büro', 150)], 450)],
		1080
	),
	id: null,
	color: null
};

it('fügt je Ebene mit Unterverbrauchern „Sonstiges“ an', () => {
	expect(consumerRows(tree).map((row) => [row.kind, row.name, row.depth, row.powerW])).toEqual([
		['root', 'Haus', 0, 1800],
		['consumer', 'Küche', 1, 120],
		['consumer', 'Obergeschoss', 1, 600],
		['consumer', 'Büro', 2, 150],
		['other', 'Sonstiges', 2, 450],
		['other', 'Sonstiges', 1, 1080]
	]);
});

it('überträgt die Abweichung auf „Sonstiges“', () => {
	const odd = node(1, 'OG', 100, [node(2, 'Büro', 150)], 0, true);
	expect(consumerRows(odd).at(-1)).toMatchObject({ kind: 'other', powerW: 0, mismatch: true });
});

it('übernimmt den Grund für einen fehlenden Wert, „Sonstiges“ hat keinen', () => {
	const parent = node(1, 'OG', null, [node(2, 'Büro', 150)]);
	const rows = consumerRows({ ...parent, note: 'kein Wert in den letzten 24 h' });
	expect(rows.map((row) => [row.name, row.note])).toEqual([
		['OG', 'kein Wert in den letzten 24 h'],
		['Büro', null],
		['Sonstiges', null]
	]);
});
