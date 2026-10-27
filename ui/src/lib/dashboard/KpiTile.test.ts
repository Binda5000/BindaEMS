import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import KpiTile from './KpiTile.svelte';

const points: [number, number][] = [
	[0, 1],
	[10, 3]
];

it('zeigt Beschriftung, Wert, Tageswert und Mini-Linie; das Test-Kennzeichen kommt vom Aufrufer', () => {
	const { container } = render(KpiTile, {
		label: 'PV',
		value: '3,23 kW',
		detail: 'heute 8,1 kWh',
		color: 'var(--pv)',
		points,
		'data-testid': 'kpi-pv'
	});
	const tile = screen.getByTestId('kpi-pv');
	expect(tile).toHaveTextContent('PV');
	expect(tile).toHaveTextContent('3,23 kW');
	expect(tile).toHaveTextContent('heute 8,1 kWh');
	expect(container.querySelector('polyline')).not.toBeNull();
});

it('lässt Mini-Linie und Tageswert ohne Daten weg', () => {
	const { container } = render(KpiTile, {
		label: 'Haus',
		value: '–',
		color: 'var(--house)',
		'data-testid': 'kpi-house'
	});
	expect(container.querySelector('svg')).toBeNull();
	expect(screen.getByTestId('kpi-house')).not.toHaveTextContent('heute');
});

it('dämpft veraltete Werte', () => {
	render(KpiTile, {
		label: 'Netz',
		value: '380 W',
		color: 'var(--grid)',
		stale: true,
		'data-testid': 'kpi-grid'
	});
	expect(screen.getByTestId('kpi-grid')).toHaveClass('stale');
});
