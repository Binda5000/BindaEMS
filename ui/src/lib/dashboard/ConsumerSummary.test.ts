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
	children: [
		{
			id: 1,
			name: 'Küche',
			color: '#4f8cff',
			power_w: 120,
			note: null,
			other_w: null,
			mismatch: false,
			children: []
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
