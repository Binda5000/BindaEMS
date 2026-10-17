import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import PeaksCard from './PeaksCard.svelte';

it('shows the peaks card only for month and year', () => {
	for (const period of ['month', 'year'] as const) {
		const { unmount } = render(PeaksCard, { stats: stats({ period }) });
		expect(screen.getByRole('heading', { name: 'Monatsspitzen' })).toBeInTheDocument();
		unmount();
	}
	for (const period of ['day', 'week'] as const) {
		const { unmount } = render(PeaksCard, { stats: stats({ period }) });
		expect(screen.queryByRole('heading', { name: 'Monatsspitzen' })).not.toBeInTheDocument();
		unmount();
	}
});

it('zeigt Monat, Spitze, Zeitpunkt und Leistungspreis', () => {
	render(PeaksCard, { stats: stats() });
	const table = screen.getByRole('table', { name: 'Monatsspitzen' });
	expect(
		within(table)
			.getAllByRole('columnheader')
			.map((cell) => cell.textContent)
	).toEqual(['Monat', 'Spitze', 'Zeitpunkt', 'Leistungspreis']);
	const cells = within(within(table).getAllByRole('row')[1]).getAllByRole('cell');
	expect(cells[0]).toHaveTextContent('Oktober 2026');
	expect(cells[1]).toHaveTextContent('5,50');
	expect(cells[2]).toHaveTextContent('04.10.2026, 04:45');
	expect(cells[3]).toHaveTextContent('33,00');
	expect(table.parentElement).toHaveClass('scroll');
});

it('zeigt ohne Spitzen keine Karte', () => {
	render(PeaksCard, { stats: stats({ month_peaks: [] }) });
	expect(screen.queryByRole('heading', { name: 'Monatsspitzen' })).not.toBeInTheDocument();
});
