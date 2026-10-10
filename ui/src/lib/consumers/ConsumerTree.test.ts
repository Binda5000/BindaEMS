import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import type { TreeNode } from '$lib/api/schemas';
import ConsumerTree from './ConsumerTree.svelte';
import { consumerRows } from './tree';

const leaf = (
	id: number,
	name: string,
	power: number | null,
	note: string | null = null
): TreeNode => ({
	id,
	name,
	color: '#4f8cff',
	power_w: power,
	note,
	other_w: null,
	mismatch: false,
	children: [],
	energy_kwh: power === null ? null : 1.25,
	energy_note: null,
	other_kwh: null
});
const tree = (mismatch = false): TreeNode => ({
	id: null,
	name: 'Haus',
	color: null,
	power_w: mismatch ? 100 : 1800,
	note: null,
	other_w: mismatch ? 0 : 1680,
	mismatch,
	children: [leaf(1, 'Küche', 120)],
	energy_kwh: 12.5,
	energy_note: null,
	other_kwh: 11.25
});

it('zeigt „Sonstiges“ und Admins die Knöpfe zum Bearbeiten', () => {
	render(ConsumerTree, {
		rows: consumerRows(tree()),
		admin: true,
		onEdit: vi.fn(),
		onDelete: vi.fn()
	});
	expect(screen.getByText('Sonstiges')).toBeInTheDocument();
	expect(screen.getAllByRole('button', { name: /Küche bearbeiten/ })).toHaveLength(1);
});

it('zeigt Lesenden keine Knöpfe', () => {
	render(ConsumerTree, {
		rows: consumerRows(tree()),
		admin: false,
		onEdit: vi.fn(),
		onDelete: vi.fn()
	});
	expect(screen.queryAllByRole('button')).toHaveLength(0);
});

it('Abweichung wird angezeigt', () => {
	render(ConsumerTree, {
		rows: consumerRows(tree(true)),
		admin: false,
		onEdit: vi.fn(),
		onDelete: vi.fn()
	});
	expect(
		screen.getByText('Unterverbraucher messen mehr als der Elternverbraucher')
	).toBeInTheDocument();
});

it('nennt den Grund, wenn ein Wert fehlt', () => {
	const root: TreeNode = {
		...tree(),
		other_w: null,
		children: [leaf(1, 'Küche', 120), leaf(2, 'Serverschrank', null, 'HA meldet kWh statt W')]
	};
	render(ConsumerTree, {
		rows: consumerRows(root),
		admin: false,
		onEdit: vi.fn(),
		onDelete: vi.fn()
	});
	expect(screen.getByText('HA meldet kWh statt W')).toBeInTheDocument();
});

it('zeigt die Energie seit Mitternacht und warum sie fehlt', () => {
	const quiet = {
		...leaf(2, 'Herd', 0),
		energy_kwh: null,
		energy_note: 'kein Verlauf seit Mitternacht'
	};
	render(ConsumerTree, {
		rows: consumerRows({ ...tree(), children: [leaf(1, 'Küche', 120), quiet] }),
		admin: false,
		onEdit: vi.fn(),
		onDelete: vi.fn()
	});
	expect(screen.getByRole('columnheader', { name: 'Heute' })).toBeInTheDocument();
	expect(screen.getByRole('row', { name: /Küche/ })).toHaveTextContent('1,25 kWh');
	expect(screen.getByRole('row', { name: /Haus/ })).toHaveTextContent('12,5 kWh');
	expect(screen.getByText('Energie: kein Verlauf seit Mitternacht')).toBeInTheDocument();
});
