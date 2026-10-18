import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import ConsumersCard from './ConsumersCard.svelte';

it('zeigt den Grund eines fehlenden Werts als Text unter dem Strich', () => {
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
	const [, kitchen, office] = within(table).getAllByRole('row');
	const reason = within(office!).getByText('Tag nicht gespeichert');
	expect(reason).toHaveClass('note');
	expect(reason.closest('td')).toHaveTextContent(/^–\s*Tag nicht gespeichert$/);
	expect(within(kitchen!).queryByText(/gespeichert/)).not.toBeInTheDocument();
	expect(screen.queryByTitle('Tag nicht gespeichert')).not.toBeInTheDocument();
});

it('sagt, wenn keine Verbraucher angelegt sind', () => {
	render(ConsumersCard, { stats: stats({ consumers: [] }) });
	expect(screen.getByText('Keine Verbraucher angelegt.')).toBeInTheDocument();
});
