import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { derived } from '$lib/testing/fixtures';
import EnergyFlow from './EnergyFlow.svelte';
import { flowBranches } from './flow';

const labels = { evcs: 'EVCS', twc: 'Wall Connector' };

it('zeigt Leistung und Richtung je Zweig', () => {
	const branches = flowBranches(derived({ pv_total_w: 3000, grid_w: 512 }), labels);
	render(EnergyFlow, { branches, stale: false });
	expect(screen.getByTestId('flow-pv')).toHaveAttribute('data-direction', 'in');
	expect(screen.getByTestId('flow-pv')).toHaveTextContent('3,00 kW');
	expect(screen.getByTestId('flow-grid')).toHaveTextContent('512 W');
});

it('veraltete Daten grauen den Energiefluss aus', () => {
	render(EnergyFlow, { branches: flowBranches(derived(), labels), stale: true });
	expect(screen.getByRole('figure')).toHaveAttribute('data-stale', 'true');
	expect(screen.getByText('veraltet')).toBeInTheDocument();
});

it('zeigt unbekannte Werte als Strich, nicht als 0', () => {
	render(EnergyFlow, { branches: flowBranches(derived({ grid_w: null }), labels), stale: false });
	expect(screen.getByTestId('flow-grid')).toHaveTextContent('–');
	expect(screen.getByTestId('flow-grid')).not.toHaveTextContent('0 W');
});
