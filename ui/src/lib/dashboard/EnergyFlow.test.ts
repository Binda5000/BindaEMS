import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { NBSP } from '$lib/format';
import { derived } from '$lib/testing/fixtures';
import EnergyFlow from './EnergyFlow.svelte';
import { flowBranches, type FlowConsumer } from './flow';

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

it('zeigt unplausible Werte mit Vorzeichen und ohne Fluss', () => {
	const branches = flowBranches(derived({ pv_total_w: -300 }), labels);
	render(EnergyFlow, { branches, stale: false });
	expect(screen.getByTestId('flow-pv')).toHaveAttribute('data-direction', 'implausible');
	expect(screen.getByTestId('flow-pv')).toHaveTextContent('300 W');
	expect(screen.getByTestId('flow-pv')).toHaveTextContent('unplausibel');
	expect(screen.getByRole('figure').getAttribute('aria-label')).toContain('PV unplausibel');
});

const inside: FlowConsumer[] = [
	{ id: 'consumer:1', name: 'Obergeschoss', color: '#4e79a7', powerW: 600 },
	{ id: 'consumer:2', name: 'Küche', color: '#59a14f', powerW: 5 },
	{ id: 'other', name: 'Sonstiges', color: null, powerW: null }
];

it('zeigt die Verbraucher unter dem Haus mit Leistung und Richtung', () => {
	const branches = flowBranches(derived({ house_load_w: 1220 }), labels);
	render(EnergyFlow, { branches, consumers: inside, stale: false });
	expect(screen.getByTestId('flow-consumer:1')).toHaveAttribute('data-direction', 'out');
	expect(screen.getByTestId('flow-consumer:1')).toHaveTextContent('600 W');
	expect(screen.getByTestId('flow-consumer:2')).toHaveAttribute('data-direction', 'idle');
	expect(screen.getByTestId('flow-other')).toHaveAttribute('data-direction', 'unknown');
	expect(screen.getByTestId('flow-other')).toHaveTextContent('–');
	expect(screen.getByRole('figure').getAttribute('aria-label')).toContain(
		`im Haus: Obergeschoss 600${NBSP}W, Küche 5${NBSP}W, Sonstiges –`
	);
});

it('kürzt lange Verbrauchernamen und nennt sie vollständig im Tooltip', () => {
	const long = Array.from({ length: 6 }, (_, index) => ({
		id: `consumer:${index}`,
		name: `Wärmepumpe Keller ${index}`,
		color: '#000000',
		powerW: 100
	}));
	render(EnergyFlow, { branches: flowBranches(derived(), labels), consumers: long, stale: false });
	const first = screen.getByTestId('flow-consumer:0');
	expect(first.querySelector('text')?.textContent?.trim()).toBe('Wärmep…');
	expect(first.querySelector('title')?.textContent).toBe(`Wärmepumpe Keller 0: 100${NBSP}W`);
});

it('ohne Verbraucher bleibt das Diagramm niedrig', () => {
	render(EnergyFlow, { branches: flowBranches(derived(), labels), stale: false });
	expect(document.querySelector('svg')?.getAttribute('viewBox')).toBe('0 0 400 334');
});
