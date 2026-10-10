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
