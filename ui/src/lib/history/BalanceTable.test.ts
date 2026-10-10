import { render, screen } from '@testing-library/svelte';
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
