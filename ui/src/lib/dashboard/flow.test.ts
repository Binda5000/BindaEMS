import { expect, it } from 'vitest';
import { limits } from '$lib/testing/fixtures';
import type { TreeNode } from '$lib/api/schemas';
import { flowConsumers, MAX_FLOW_CONSUMERS, wallboxLabels } from './flow';

const labels = { evcs: 'EVCS', twc: 'Garage' };

it('benennt Wallboxen mit ihrem eigenen Namen, sonst nach ihrem Typ', () => {
	// Demo-Welt: der Wall Connector heißt „Garage“, die EVCS hat keinen eigenen Namen
	expect(wallboxLabels(limits())).toEqual(labels);
	const unnamed = limits();
	unnamed.wallboxes.twc.name = null;
	expect(wallboxLabels(unnamed).twc).toBe('Wall Connector');
});

const child = (id: number, power: number | null): TreeNode => ({
	id,
	name: `V${id}`,
	color: '#000000',
	power_w: power,
	note: null,
	other_w: null,
	mismatch: false,
	children: [],
	energy_kwh: null,
	energy_note: null,
	other_kwh: null
});
const house = (children: TreeNode[], other: number | null): TreeNode => ({
	...child(0, 2000),
	id: null,
	name: 'Haus',
	color: null,
	other_w: other,
	children
});

it('zeigt die Verbraucher der ersten Ebene und „Sonstiges“', () => {
	expect(flowConsumers(undefined)).toEqual([]);
	expect(flowConsumers(house([], null))).toEqual([]);
	expect(flowConsumers(house([child(1, 600), child(2, 150)], 1250))).toEqual([
		{ id: 'consumer:1', name: 'V1', color: '#000000', powerW: 600 },
		{ id: 'consumer:2', name: 'V2', color: '#000000', powerW: 150 },
		{ id: 'other', name: 'Sonstiges', color: null, powerW: 1250 }
	]);
});

it('fasst bei zu vielen Verbrauchern die kleinsten zusammen', () => {
	const many = [100, 900, 50, 700, 300, 200].map((power, index) => child(index + 1, power));
	const items = flowConsumers(house(many, 0));
	expect(items).toHaveLength(MAX_FLOW_CONSUMERS);
	expect(items.map((item) => [item.name, item.powerW])).toEqual([
		['V2', 900],
		['V4', 700],
		['V5', 300],
		['V6', 200],
		['2 weitere', 150],
		['Sonstiges', 0]
	]);
	const unknown = flowConsumers(house([...many.slice(0, 5), child(7, null)], 0));
	expect(unknown.find((item) => item.id === 'more')?.powerW).toBeNull();
});
