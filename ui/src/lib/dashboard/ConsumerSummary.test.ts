import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { TreeNode } from '$lib/api/schemas';
import ConsumerSummary from './ConsumerSummary.svelte';

const tree: TreeNode = {
	id: null,
	name: 'Haus',
	color: null,
	power_w: 1800,
	note: null,
	other_w: 1680,
	mismatch: false,
	energy_kwh: 14.2,
	energy_note: null,
	other_kwh: 13,
	children: [
		{
			id: 1,
			name: 'Küche',
			color: '#4f8cff',
			power_w: 120,
			note: null,
			other_w: null,
			mismatch: false,
			children: [],
			energy_kwh: 1.2,
			energy_note: null,
			other_kwh: null
		}
	]
};

it('zeigt einen Fehler neben den alten Werten und kennzeichnet sie als veraltet', () => {
	render(ConsumerSummary, { tree, error: new ApiError(502, 'Server nicht erreichbar') });
	expect(screen.getByText('Server nicht erreichbar')).toBeInTheDocument();
	expect(screen.getByText('Küche')).toBeInTheDocument();
	expect(screen.getByText('veraltet')).toBeInTheDocument();
});

it('zeigt aktuelle Werte ohne Kennzeichnung und ohne Daten nur den Fehler', () => {
	const { unmount } = render(ConsumerSummary, { tree, error: null });
	expect(screen.queryByText('veraltet')).not.toBeInTheDocument();
	unmount();
	render(ConsumerSummary, { tree: undefined, error: new ApiError(502, 'Server nicht erreichbar') });
	expect(screen.getByText('Server nicht erreichbar')).toBeInTheDocument();
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('zeigt Leistung und Energie seit Mitternacht', () => {
	render(ConsumerSummary, { tree, error: null });
	expect(screen.getByRole('row', { name: /Küche/ })).toHaveTextContent('120 W');
	expect(screen.getByRole('row', { name: /Küche/ })).toHaveTextContent('1,20 kWh');
	expect(screen.getByRole('row', { name: /Haus/ })).toHaveTextContent('14,2 kWh');
});
