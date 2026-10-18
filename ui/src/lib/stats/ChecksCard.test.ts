import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import ChecksCard from './ChecksCard.svelte';

it('zeigt die Größen mit Abrechnung, Zähler und Abweichung', () => {
	render(ChecksCard, { stats: stats(), wallboxNames: { evcs: 'Carport', twc: 'Garage' } });
	expect(screen.getByRole('heading', { name: 'Abgleich mit den Zählern' })).toBeInTheDocument();
	const table = screen.getByRole('table', { name: 'Abgleich mit den Zählern' });
	expect(
		within(table)
			.getAllByRole('columnheader')
			.map((cell) => cell.textContent)
	).toEqual(['Größe', 'Abrechnung', 'Zähler', 'Abweichung']);
	const labels = within(table)
		.getAllByRole('row')
		.slice(1)
		.map((row) => within(row).getAllByRole('cell')[0].textContent);
	expect(labels).toEqual([
		'Netzbezug',
		'Einspeisung',
		'PV (ohne Markierung)',
		'Wallbox Carport',
		'Wallbox Garage'
	]);
	expect(table.parentElement).toHaveClass('scroll');
});

it('names flagged days in the checks card', () => {
	render(ChecksCard, {
		stats: stats({ checks: { ...stats().checks, flagged_days: ['2026-10-12'] } })
	});
	expect(screen.getByText('Mehr als 1 % Abweichung am 12.10.')).toBeInTheDocument();
});

it('sagt ohne geprüfte Tage, dass noch nichts abgeglichen ist', () => {
	render(ChecksCard, { stats: stats({ checks: { days: 0, flagged_days: [], totals: [] } }) });
	expect(screen.getByText('Noch kein Tag abgeglichen.')).toBeInTheDocument();
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('zeigt ohne markierte Tage keinen Hinweis', () => {
	render(ChecksCard, { stats: stats({ checks: { ...stats().checks, flagged_days: [] } }) });
	expect(screen.queryByText(/Mehr als 1 %/)).not.toBeInTheDocument();
});

it('sagt bei geprüften Tagen ohne Summen und Markierungen, dass Zählerwerte fehlen', () => {
	render(ChecksCard, { stats: stats({ checks: { days: 3, flagged_days: [], totals: [] } }) });
	expect(screen.getByText('Keine Zählerwerte für diesen Zeitraum.')).toHaveClass('muted');
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('zeigt keine Tabelle mit 0,00 kWh, wenn an keinem Tag ein Zählerstand da ist', () => {
	// die Form des Servers bei lauter „Zählerstand fehlt“: je Größe eine Summe ohne Zähler
	const totals = ['import', 'export', 'pv', 'wb:evcs'].map((signal) => ({
		signal,
		ledger_kwh: 0,
		counter_kwh: null,
		deviation_pct: null
	}));
	render(ChecksCard, { stats: stats({ checks: { days: 3, flagged_days: [], totals } }) });
	expect(screen.getByText('Keine Zählerwerte für diesen Zeitraum.')).toHaveClass('muted');
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('nennt unter der Tabelle die Größen ohne Zählerwert', () => {
	// die Form des Servers: eine Größe ohne Tag mit Zählerwert neben einer mit Wert
	const totals = [
		{ signal: 'import', ledger_kwh: 10.2, counter_kwh: 10, deviation_pct: 2 },
		{ signal: 'wb:evcs', ledger_kwh: 0, counter_kwh: null, deviation_pct: null }
	];
	render(ChecksCard, {
		stats: stats({ checks: { days: 3, flagged_days: [], totals } }),
		wallboxNames: { evcs: 'EVCS' }
	});
	const table = screen.getByRole('table', { name: 'Abgleich mit den Zählern' });
	expect(within(table).getAllByRole('row')).toHaveLength(2);
	expect(screen.getByText('Ohne Zählerwerte: Wallbox EVCS.')).toHaveClass('muted');
	expect(screen.queryByText('Keine Zählerwerte für diesen Zeitraum.')).not.toBeInTheDocument();
});
