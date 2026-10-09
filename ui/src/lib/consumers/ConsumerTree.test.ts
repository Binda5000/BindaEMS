import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import type { TreeNode } from '$lib/api/schemas';
import ConsumerTree from './ConsumerTree.svelte';
import { consumerRows } from './tree';

const leaf = (id: number, name: string, power: number): TreeNode => ({
	id,
	name,
	color: '#4f8cff',
	power_w: power,
	other_w: null,
	mismatch: false,
	children: []
});
const tree = (mismatch = false): TreeNode => ({
	id: null,
	name: 'Haus',
	color: null,
	power_w: mismatch ? 100 : 1800,
	other_w: mismatch ? 0 : 1680,
	mismatch,
	children: [leaf(1, 'Küche', 120)]
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
