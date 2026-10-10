import { expect, it } from 'vitest';
import { derived, limits } from '$lib/testing/fixtures';
import type { TreeNode } from '$lib/api/schemas';
import { flowBranches, flowConsumers, MAX_FLOW_CONSUMERS, wallboxLabels } from './flow';

const labels = { evcs: 'EVCS', twc: 'Garage' };
const midday = derived({
	pv_total_w: 3000,
	grid_w: 512,
	battery_w: -200,
	house_load_w: 1800,
	wallbox_w: { evcs: 0, twc: 1380 }
});

it('benennt Wallboxen mit ihrem eigenen Namen, sonst nach ihrem Typ', () => {
	// Demo-Welt: der Wall Connector heißt „Garage“, die EVCS hat keinen eigenen Namen
	expect(wallboxLabels(limits())).toEqual(labels);
	const unnamed = limits();
	unnamed.wallboxes.twc.name = null;
	expect(wallboxLabels(unnamed).twc).toBe('Wall Connector');
});

it('zeigt Quellen zum Hausanschluss hin und Verbraucher von ihm weg', () => {
	expect(flowBranches(midday, labels)).toEqual([
		{ id: 'pv', label: 'PV', caption: 'PV', powerW: 3000, direction: 'in' },
		{ id: 'grid', label: 'Netz', caption: 'Bezug', powerW: 512, direction: 'in' },
		{ id: 'battery', label: 'Akku', caption: 'entlädt', powerW: 200, direction: 'in' },
		{ id: 'house', label: 'Haus', caption: 'Haus', powerW: 1800, direction: 'out' },
		{ id: 'wallbox:evcs', label: 'EVCS', caption: 'EVCS', powerW: 0, direction: 'idle' },
		{
			id: 'wallbox:twc',
			label: 'Garage',
			caption: 'Garage',
			powerW: 1380,
			direction: 'out'
		}
	]);
});

it('kennt Einspeisung und Laden', () => {
	const [, grid, battery] = flowBranches({ ...midday, grid_w: -1500, battery_w: 2500 }, labels);
	expect([grid.caption, grid.direction, grid.powerW]).toEqual(['Einspeisung', 'out', 1500]);
	expect([battery.caption, battery.direction]).toEqual(['lädt', 'out']);
});

it('unbekannte Werte zeigen einen Strich', () => {
	const branches = flowBranches({ ...midday, grid_w: null, wallbox_w: { evcs: 0 } }, labels);
	expect(branches.find((b) => b.id === 'grid')).toMatchObject({
		powerW: null,
		direction: 'unknown',
		caption: 'Netz'
	});
	expect(branches.find((b) => b.id === 'wallbox:twc')).toMatchObject({
		powerW: null,
		direction: 'unknown'
	});
	expect(flowBranches(null, labels).every((b) => b.direction === 'unknown')).toBe(true);
});

it('kennzeichnet negative PV-, Haus- und Wallboxwerte als unplausibel statt als Rückfluss', () => {
	// Zeitversatz oder Messfehler: Strom fließt nicht vom Haus ins Netz zurück
	const branches = flowBranches(
		{ ...midday, pv_total_w: -300, house_load_w: -150, wallbox_w: { evcs: -500, twc: -10 } },
		labels
	);
	const byId = Object.fromEntries(branches.map((b) => [b.id, b]));
	expect([byId.pv.direction, byId.pv.powerW]).toEqual(['implausible', -300]);
	expect([byId.house.direction, byId.house.powerW]).toEqual(['implausible', -150]);
	expect([byId['wallbox:evcs'].direction, byId['wallbox:evcs'].powerW]).toEqual([
		'implausible',
		-500
	]);
	expect(byId['wallbox:twc'].direction).toBe('idle'); // Rauschen um 0
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
