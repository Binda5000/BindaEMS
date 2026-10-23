import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import type { Palette } from '$lib/charts/palette';
import { limits, stats } from '$lib/testing/fixtures';
import VehicleTrendCard from './VehicleTrendCard.svelte';
import { vehicleChoices } from './vehicles';

vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	load: '#80f',
	series: ['#00f', '#0a0']
};

const entry = (key: string) => ({
	key,
	kwh: 2,
	pv_kwh: 1,
	battery_kwh: 0,
	grid_kwh: 1,
	cost_eur: 0.4
});

/** Woche mit Ladung von Tesla und Gast */
const week = () =>
	stats({
		period: 'week',
		vehicle_buckets: [
			{ start: '2026-10-05', vehicles: [entry('tesla'), entry('guest')] },
			{ start: '2026-10-06', vehicles: [] }
		]
	});

function setup(props: Partial<{ stats: ReturnType<typeof stats>; vehicle: string | null }> = {}) {
	const onselect = vi.fn();
	render(VehicleTrendCard, {
		stats: week(),
		vehicle: null,
		choices: vehicleChoices(limits(), props.vehicle ?? null),
		colorOf: () => '#123456',
		palette,
		title: 'KW 41 · 05.10.–11.10.2026',
		onselect,
		...props
	});
	return onselect;
}

it('zeigt die Auswahl als Knöpfe mit aria-pressed', async () => {
	const onselect = setup();
	expect(screen.getByRole('heading', { name: 'Laden je Auto' })).toBeInTheDocument();
	const group = screen.getByRole('group', { name: 'Auto wählen' });
	expect(within(group).getByRole('button', { name: 'Alle' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	expect(within(group).getByRole('button', { name: 'e-Golf' })).toHaveAttribute(
		'aria-pressed',
		'false'
	);
	await fireEvent.click(within(group).getByRole('button', { name: 'e-Golf' }));
	expect(onselect).toHaveBeenCalledWith('egolf');
	await fireEvent.click(within(group).getByRole('button', { name: 'Alle' }));
	expect(onselect).toHaveBeenLastCalledWith(null);
});

it('drückt das gewählte Auto', () => {
	setup({ vehicle: 'tesla' });
	const group = screen.getByRole('group', { name: 'Auto wählen' });
	expect(within(group).getByRole('button', { name: 'Tesla Model 3' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	expect(within(group).getByRole('button', { name: 'Alle' })).toHaveAttribute(
		'aria-pressed',
		'false'
	);
});

it('schaltet zwischen kWh und €', async () => {
	setup();
	const group = screen.getByRole('group', { name: 'Einheit' });
	expect(within(group).getByRole('button', { name: 'kWh' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await fireEvent.click(within(group).getByRole('button', { name: '€' }));
	expect(within(group).getByRole('button', { name: '€' })).toHaveAttribute('aria-pressed', 'true');
	expect(within(group).getByRole('button', { name: 'kWh' })).toHaveAttribute(
		'aria-pressed',
		'false'
	);
});

it('zeigt den Verlauf als Diagramm, benannt nach dem Zeitraum', () => {
	setup();
	expect(
		screen.getByRole('img', { name: 'Laden je Auto KW 41 · 05.10.–11.10.2026' })
	).toBeInTheDocument();
});

it('zeigt bei Tag keinen Verlauf', () => {
	setup({ stats: stats({ period: 'day', vehicle_buckets: [] }) });
	expect(screen.getByText('Den Verlauf zeigen Woche, Monat und Jahr.')).toBeInTheDocument();
	expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('zeigt ohne Ladung des Autos keinen leeren Verlauf', () => {
	setup({ vehicle: 'egolf' });
	expect(screen.getByText('Im Zeitraum wurde kein Auto geladen.')).toBeInTheDocument();
	expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('zeigt ohne Ladung irgendeines Autos keinen leeren Verlauf', () => {
	setup({
		stats: stats({ period: 'month', vehicle_buckets: [{ start: '2026-10-01', vehicles: [] }] })
	});
	expect(screen.getByText('Im Zeitraum wurde kein Auto geladen.')).toBeInTheDocument();
});
