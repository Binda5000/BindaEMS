import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import VehicleCard from './VehicleCard.svelte';

it('listet die Autos mit PV, Akku, Netz, Kosten und Sessions und die Zeile Akku → Autos', () => {
	render(VehicleCard, { stats: stats() });
	expect(screen.getByRole('heading', { name: 'Autos' })).toBeInTheDocument();
	const table = screen.getByRole('table', { name: 'Autos' });
	expect(
		within(table)
			.getAllByRole('columnheader')
			.map((cell) => cell.textContent)
	).toEqual(['Auto', 'Energie', 'PV', 'Akku', 'Netz', 'Kosten', 'ct/kWh', 'Sessions']);
	const rows = within(table).getAllByRole('row').slice(1);
	expect(rows).toHaveLength(3);
	const tesla = within(rows[0]).getAllByRole('cell');
	expect(tesla[0]).toHaveTextContent('Tesla Model 3');
	expect(tesla[6]).toHaveTextContent('4,87'); // 0,974 € / 20,01 kWh
	expect(tesla[7]).toHaveTextContent('2');
	expect(within(rows[1]).getAllByRole('cell')[0]).toHaveTextContent('Gast');
	const battery = within(rows[2]).getAllByRole('cell');
	expect(battery[0]).toHaveTextContent('Akku → Autos');
	expect(battery[1]).toHaveTextContent('12,9');
});

it('scrollt die breite Tabelle in der Karte', () => {
	render(VehicleCard, { stats: stats() });
	expect(screen.getByRole('table', { name: 'Autos' }).parentElement).toHaveClass('scroll');
});

it('sagt, wenn im Zeitraum kein Auto geladen wurde', () => {
	render(VehicleCard, { stats: stats({ vehicles: [], battery_to_vehicles_kwh: 0 }) });
	expect(screen.getByText('Im Zeitraum wurde kein Auto geladen.')).toBeInTheDocument();
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});
