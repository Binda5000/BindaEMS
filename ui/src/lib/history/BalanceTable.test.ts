import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { daySummary } from '$lib/testing/fixtures';
import BalanceTable from './BalanceTable.svelte';
import { balanceRows } from './balance';

it('nennt die Zählerwerte je Tag als Tageswerte, nicht als Zählerstände', () => {
	// counter_kwh ist die Differenz der Zähler über den Tag, kein abgelesener Stand
	const rows = balanceRows([daySummary({ counter_kwh: { 'grid.energy_import_kwh': 12.345 } })]);
	render(BalanceTable, { rows });
	expect(screen.getByText(/Tageswerte der Zähler für den Abgleich mit VRM/)).toBeInTheDocument();
	expect(screen.getByText('Tageswerte')).toBeInTheDocument();
	expect(screen.queryByText(/Zählerstände/)).not.toBeInTheDocument();
});

it('wird am Handy zur Karte: Klasse „stack“ und data-label wie die Spaltenköpfe', () => {
	render(BalanceTable, { rows: balanceRows([daySummary()]) });
	const table = screen.getByRole('table');
	expect(table).toHaveClass('stack');
	const heads = within(table)
		.getAllByRole('columnheader')
		.map((head) => head.textContent);
	const cells = within(within(table).getAllByRole('row')[1]!).getAllByRole('cell');
	// die erste Zelle (Tag) ist der Kartentitel, alle übrigen tragen ihre Beschriftung
	expect(cells[0]).not.toHaveAttribute('data-label');
	expect(cells.slice(1).map((cell) => cell.getAttribute('data-label'))).toEqual(heads.slice(1));
});
