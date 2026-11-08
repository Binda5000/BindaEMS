import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import PvCard from './PvCard.svelte';

const plant = { id: 1, name: 'Dach', parent_id: null, kwp: 4.1, note: null };

it('zeigt Strings eingerückt unter ihrer Anlage mit kWh und kWh/kWp', () => {
	render(PvCard, {
		stats: stats({
			pv_sources: [
				{ ...plant, kwh: 25, kwh_per_kwp: 6.1 },
				{
					id: 3,
					name: 'Balkon',
					parent_id: null,
					kwp: null,
					kwh: 1.5,
					kwh_per_kwp: null,
					note: null
				},
				{ id: 2, name: 'Süd', parent_id: 1, kwp: 2, kwh: 14.2, kwh_per_kwp: 7.1, note: null }
			]
		})
	});
	expect(screen.getByRole('heading', { name: 'PV je Anlage' })).toBeInTheDocument();
	const table = screen.getByRole('table', { name: 'PV je Anlage' });
	const [, dach, sued, balkon] = within(table).getAllByRole('row');
	expect(within(dach!).getByText('25,0 kWh')).toBeInTheDocument();
	expect(within(dach!).getByText('6,10 kWh/kWp')).toBeInTheDocument();
	expect(within(sued!).getByText('Süd').closest('td')).toHaveClass('child');
	expect(within(dach!).getByText('Dach').closest('td')).not.toHaveClass('child');
	expect(within(balkon!).getByText('Balkon')).toBeInTheDocument();
	expect(within(balkon!).getByText('1,50 kWh')).toBeInTheDocument();
});

it('zeigt ohne Wert einen Strich und klein den Grund', () => {
	render(PvCard, {
		stats: stats({
			pv_sources: [{ ...plant, kwh: null, kwh_per_kwp: null, note: 'Tag nicht gespeichert' }]
		})
	});
	const [, row] = within(screen.getByRole('table')).getAllByRole('row');
	const reason = within(row!).getByText('Tag nicht gespeichert');
	expect(reason).toHaveClass('note');
	expect(row).toHaveTextContent(/–/);
});

it('fehlt ganz, wenn keine Anlagen angelegt sind', () => {
	render(PvCard, { stats: stats({ pv_sources: [] }) });
	expect(screen.queryByRole('heading', { name: 'PV je Anlage' })).not.toBeInTheDocument();
});
