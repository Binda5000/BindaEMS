import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import CostCard from './CostCard.svelte';

it('zeigt die Zeilen als Tabelle mit der Summe und darunter die Ersparnisse', () => {
	render(CostCard, { stats: stats() });
	expect(screen.getByRole('heading', { name: 'Kosten' })).toBeInTheDocument();
	const rows = screen.getAllByRole('row').map((row) => within(row).getAllByRole('cell'));
	expect(rows.map((cells) => cells[0].textContent)).toEqual([
		'Bezug',
		'Einspeiseerlös',
		'Netto',
		'Grundgebühren',
		'Leistungspreis',
		'Summe',
		'Ersparnis ohne Anlage',
		'Ersparnis bei gleichem Netzbezug'
	]);
	expect(screen.getByRole('cell', { name: 'Grundgebühren' })).toBeInTheDocument();
	expect(rows[5][1]).toHaveTextContent('44,33');
	expect(rows[6][1]).toHaveTextContent('59,13');
	expect(rows[7][1]).toHaveTextContent('11,65');
});

it('lässt den Leistungspreis in der Woche weg und zeigt unbekannte Beträge als Strich', () => {
	const base = stats();
	render(CostCard, {
		stats: {
			...base,
			period: 'week',
			money: { ...base.money, power_eur: null, net_eur: null, total_eur: null }
		}
	});
	expect(screen.queryByRole('cell', { name: 'Leistungspreis' })).not.toBeInTheDocument();
	const sum = screen.getByRole('cell', { name: 'Summe' }).closest('tr') as HTMLElement;
	expect(within(sum).getAllByRole('cell')[1]).toHaveTextContent('–');
});

it('benennt seine beiden Tabellen', () => {
	render(CostCard, { stats: stats() });
	expect(screen.getByRole('table', { name: 'Kosten' })).toBeInTheDocument();
	expect(screen.getByRole('table', { name: 'Ersparnis' })).toBeInTheDocument();
});
