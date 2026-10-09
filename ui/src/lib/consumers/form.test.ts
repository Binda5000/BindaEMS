import { expect, it } from 'vitest';
import type { Consumer } from '$lib/api/schemas';
import { draftToInput, emptyDraft, parentOptions } from './form';

const consumer = (id: number, name: string, parent: number | null): Consumer => ({
	id,
	name,
	group: null,
	parent_id: parent,
	color: '#4f8cff',
	source_kind: 'core',
	power_ref: `load.k${id}.power_w`,
	power_unit: null,
	sort: 0
});

it('übernimmt gültige Angaben', () => {
	const draft = {
		...emptyDraft(),
		name: ' Küche ',
		sourceKind: 'ha' as const,
		powerRef: 'sensor.kueche_power',
		powerUnit: 'W' as const
	};
	expect(draftToInput(draft)).toEqual({
		input: {
			name: 'Küche',
			group: null,
			parent_id: null,
			color: '#4f8cff',
			source_kind: 'ha',
			power_ref: 'sensor.kueche_power',
			power_unit: 'W',
			sort: 0
		}
	});
});

it('prüft core-Signale wie der Server', () => {
	expect(draftToInput({ ...emptyDraft(), name: 'OG', powerRef: 'load.og' })).toEqual({
		errors: { powerRef: 'core-Signal muss auf .power_w enden, z. B. load.obergeschoss.power_w' }
	});
});

it('verlangt bei HA-Entitäten Format und Einheit', () => {
	expect(
		draftToInput({ ...emptyDraft(), name: 'K', sourceKind: 'ha', powerRef: 'kueche' })
	).toEqual({
		errors: {
			powerRef: 'HA-Entität als domain.objekt angeben, z. B. sensor.kueche',
			powerUnit: 'Einheit wählen (W oder kW)'
		}
	});
});

it('meldet Name, Farbe und Reihenfolge', () => {
	const draft = {
		...emptyDraft(),
		name: ' ',
		color: 'blau',
		sort: '1,5',
		powerRef: 'load.og.power_w'
	};
	expect(draftToInput(draft)).toEqual({
		errors: {
			name: 'Name fehlt',
			color: 'Farbe als #rrggbb angeben',
			sort: 'ganze Zahl zwischen -1000000 und 1000000'
		}
	});
});

it('bietet keine Elternelemente an, die einen Kreis ergäben', () => {
	const consumers = [
		consumer(1, 'OG', null),
		consumer(2, 'Büro', 1),
		consumer(3, 'Schreibtisch', 2),
		consumer(4, 'Küche', null)
	];
	expect(parentOptions(consumers, 1).map((option) => option.id)).toEqual([4]);
	expect(parentOptions(consumers, null).map((option) => option.id)).toEqual([1, 2, 3, 4]);
});
