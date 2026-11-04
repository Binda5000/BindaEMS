import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { OpsSwitch } from '$lib/api/schemas';
import SwitchesCard from './SwitchesCard.svelte';

const SWITCHES: OpsSwitch[] = [
	{
		id: 'control',
		label: 'Regelbetrieb',
		group: 'Regelung',
		value: 'an',
		factory: 'aus',
		changed: true,
		since: '2026-11-03T18:45:00Z',
		actor: 'Binda',
		source: 'ui',
		link: '/system'
	},
	{
		id: 'grid_charge',
		label: 'Netzladen des Akkus',
		group: 'Akku',
		value: 'aus',
		factory: 'aus',
		changed: false,
		since: null,
		actor: null,
		source: null,
		link: '/zeitplan'
	}
];

const NOW = new Date('2026-11-04T09:00:00Z');

it('zeigt die Schalter je Gruppe und markiert geänderte', () => {
	render(SwitchesCard, { switches: { switches: SWITCHES }, error: null, now: NOW });
	expect(screen.getByRole('heading', { level: 2, name: 'Schalter' })).toBeInTheDocument();
	const tables = screen.getAllByRole('table');
	expect(tables.map((table) => table.getAttribute('aria-label'))).toEqual(['Regelung', 'Akku']);
	expect(screen.getByRole('heading', { level: 3, name: 'Regelung' })).toBeInTheDocument();

	const control = within(tables[0]!).getAllByRole('row')[1]!;
	expect(within(control).getByText('Regelbetrieb')).toBeInTheDocument();
	expect(within(control).getByText('geändert')).toBeInTheDocument();
	expect(within(control).getByText('seit 3.11. 19:45 · Binda (UI)')).toBeInTheDocument();
	expect(within(control).getByRole('link', { name: 'System öffnen' })).toHaveAttribute(
		'href',
		'/system'
	);

	const grid = within(tables[1]!).getAllByRole('row')[1]!;
	expect(within(grid).queryByText('geändert')).toBeNull();
	expect(within(grid).getByText('seit Einrichtung')).toBeInTheDocument();
	for (const name of ['Schalter', 'Stand', 'Ab Werk', 'Seit', 'Stellen']) {
		expect(within(tables[0]!).getByRole('columnheader', { name })).toBeInTheDocument();
	}
	expect(screen.getByText('1 geändert')).toBeInTheDocument();
});
