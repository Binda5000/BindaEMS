import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { PriceSlot } from '$lib/api/schemas';
import PriceNow from './PriceNow.svelte';

const now: PriceSlot = {
	start: '2026-10-09T08:00:00+00:00',
	spot_net_ct: 9,
	import_net_ct: 11.2,
	import_gross_ct: 13.44,
	feed_in_ct: 7.3,
	origin: 'fallback',
	missing: ['grid_loss']
};

it('zeigt den Preis jetzt, die Ersatzquelle und fehlende Tarifwerte', () => {
	render(PriceNow, { data: { now, next_3h: [] }, error: null });
	expect(screen.getByText('13,44 ct/kWh')).toBeInTheDocument();
	expect(screen.getByText('Ersatzquelle')).toBeInTheDocument();
	expect(
		screen.getByText('Nicht alle Tarifbestandteile eingetragen (als 0 gerechnet)')
	).toBeInTheDocument();
});

it('zeigt ohne Preis einen Strich und den Fehler', () => {
	render(PriceNow, {
		data: { now: null, next_3h: [] },
		error: new ApiError(0, 'Keine Verbindung zum Server')
	});
	expect(screen.getByTestId('price-now')).toHaveTextContent('–');
	expect(screen.getByText('Keine Verbindung zum Server')).toBeInTheDocument();
});
