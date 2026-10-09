import { expect, it } from 'vitest';
import { derived, limits } from '$lib/testing/fixtures';
import { flowBranches, wallboxLabels } from './flow';

const labels = { evcs: 'EVCS', twc: 'Wall Connector' };
const midday = derived({
	pv_total_w: 3000,
	grid_w: 512,
	battery_w: -200,
	house_load_w: 1800,
	wallbox_w: { evcs: 0, twc: 1380 }
});

it('benennt Wallboxen nach ihrem Typ', () => {
	expect(wallboxLabels(limits())).toEqual(labels);
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
			label: 'Wall Connector',
			caption: 'Wall Connector',
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
