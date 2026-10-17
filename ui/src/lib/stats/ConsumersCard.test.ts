import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import ConsumersCard from './ConsumersCard.svelte';

it('shows the consumer reason as title when kWh is missing', () => {
	render(ConsumersCard, {
		stats: stats({
			consumers: [
				{ id: 1, name: 'Küche', kwh: 3.456, note: null },
				{ id: 2, name: 'Büro', kwh: null, note: 'Tag nicht gespeichert' }
			]
		})
	});
	expect(screen.getByRole('heading', { name: 'Verbraucher' })).toBeInTheDocument();
	const table = screen.getByRole('table', { name: 'Verbraucher' });
	expect(
		within(table)
			.getAllByRole('columnheader')
			.map((cell) => cell.textContent)
	).toEqual(['Verbraucher', 'Energie']);
	expect(screen.getByText('3,46 kWh')).toBeInTheDocument();
	const missing = screen.getByTitle('Tag nicht gespeichert');
	expect(missing).toHaveTextContent('–');
});

it('sagt, wenn keine Verbraucher angelegt sind', () => {
	render(ConsumersCard, { stats: stats({ consumers: [] }) });
	expect(screen.getByText('Keine Verbraucher angelegt.')).toBeInTheDocument();
});
