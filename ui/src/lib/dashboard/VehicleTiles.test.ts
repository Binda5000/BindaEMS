import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { VehicleCard } from './vehicles';
import VehicleTiles from './VehicleTiles.svelte';

const tesla: VehicleCard = {
	id: 'tesla',
	name: 'Tesla Model 3',
	socPct: 70,
	socQuality: 'stale',
	guest: false,
	status: 'Garage · lädt 1,38 kW',
	mode: 'PV-Überschuss'
};
const guest: VehicleCard = {
	id: 'guest:twc',
	name: 'Gast',
	socPct: null,
	socQuality: 'missing',
	guest: true,
	status: 'Garage · angesteckt',
	mode: null
};

it('zeigt je Auto den Ring mit dem Ladestand, den Stand, den Modus und „SOC veraltet“', () => {
	render(VehicleTiles, { cards: [tesla], stale: false, error: null });
	expect(screen.getByRole('heading', { name: 'Autos' })).toBeInTheDocument();
	const ring = screen.getByRole('img');
	expect(ring).toHaveTextContent('70');
	expect(ring).toHaveAccessibleName(/^Tesla Model 3 70\s%$/);
	expect(screen.getByText('Tesla Model 3')).toBeInTheDocument();
	expect(screen.getByText('Garage · lädt 1,38 kW')).toBeInTheDocument();
	expect(screen.getByText('PV-Überschuss')).toBeInTheDocument();
	expect(screen.getByText('SOC veraltet')).toBeInTheDocument();
});

it('zeigt „kein SOC“ mit Strich im Ring, wenn der Ladestand fehlt', () => {
	render(VehicleTiles, {
		cards: [{ ...tesla, socPct: null, socQuality: 'missing' }],
		stale: false,
		error: null
	});
	expect(screen.getByRole('img')).toHaveTextContent('–');
	expect(screen.getByText('kein SOC')).toBeInTheDocument();
});

it('zeigt den Gast ohne Ring', () => {
	render(VehicleTiles, { cards: [tesla, guest], stale: false, error: null });
	const item = screen.getByText('Gast').closest('li');
	if (item === null) throw new Error('Gast-Kachel fehlt');
	expect(within(item).queryByRole('img')).not.toBeInTheDocument();
	expect(within(item).getByText('Garage · angesteckt')).toBeInTheDocument();
	expect(within(item).queryByText(/SOC/)).not.toBeInTheDocument();
});

it('sagt, wenn keine Autos eingetragen sind, und verweist zum Laden', () => {
	render(VehicleTiles, { cards: [], stale: false, error: null });
	expect(screen.getByText('Keine Autos eingetragen.')).toBeInTheDocument();
	expect(screen.getByRole('link', { name: 'Zum Laden' })).toHaveAttribute('href', '/laden');
});

it('zeigt „Lädt …“, solange die Fahrzeuge fehlen', () => {
	render(VehicleTiles, { cards: undefined, stale: false, error: null });
	expect(screen.getByText('Lädt …')).toBeInTheDocument();
	expect(screen.queryByText('Keine Autos eingetragen.')).not.toBeInTheDocument();
});

it('nennt bei einem Fehler nur den Fehler, nie „Keine Autos eingetragen.“', () => {
	for (const cards of [undefined, []]) {
		const { unmount } = render(VehicleTiles, {
			cards,
			stale: false,
			error: new ApiError(502, 'Server nicht erreichbar (HTTP 502)')
		});
		expect(
			screen.getByText(/Fahrzeuge nicht geladen: Server nicht erreichbar/)
		).toBeInTheDocument();
		expect(screen.queryByText('Keine Autos eingetragen.')).not.toBeInTheDocument();
		expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();
		unmount();
	}
});

it('zeigt den Fehler', () => {
	render(VehicleTiles, {
		cards: [],
		stale: false,
		error: new ApiError(502, 'Server nicht erreichbar (HTTP 502)')
	});
	expect(screen.getByText(/Server nicht erreichbar/)).toBeInTheDocument();
});
