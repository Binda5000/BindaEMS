import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { PriceSlot } from '$lib/api/schemas';
import PriceTile from './PriceTile.svelte';

const START = Date.parse('2026-10-09T08:00:00+00:00');

function slot(index: number, ct: number, extra: Partial<PriceSlot> = {}): PriceSlot {
	return {
		start: new Date(START + index * 900_000).toISOString(),
		spot_net_ct: 9,
		import_net_ct: 11.2,
		import_gross_ct: ct,
		feed_in_ct: 7.3,
		origin: 'primary',
		missing: [],
		...extra
	};
}

it('zeigt Preis, Einspeisung, Mini-Balken und den günstigsten Zeitpunkt', () => {
	render(PriceTile, {
		data: { now: slot(0, 30), next_3h: [slot(1, 20), slot(2, 40)] }, // next_3h ab 10:15 wie vom Server
		error: null
	});
	expect(screen.getByTestId('price-now')).toHaveTextContent(/^30,00\sct\/kWh$/);
	expect(screen.getByText(/^Einspeisung 7,30\sct\/kWh$/)).toBeInTheDocument();
	expect(screen.getByText('am günstigsten um 10:15')).toBeInTheDocument();
	expect(screen.getByRole('img', { name: /Preise der nächsten 3 Stunden/ })).toBeInTheDocument();
});

it('sagt „jetzt am günstigsten“', () => {
	render(PriceTile, {
		data: { now: slot(0, 10), next_3h: [slot(1, 20), slot(2, 15)] },
		error: null
	});
	expect(screen.getByText('jetzt am günstigsten')).toBeInTheDocument();
});

it('markiert Ersatzquelle und unvollständigen Tarif', () => {
	render(PriceTile, {
		data: { now: slot(0, 30, { origin: 'fallback', missing: ['grid_loss'] }), next_3h: [] },
		error: null
	});
	expect(screen.getByText('Ersatzquelle')).toBeInTheDocument();
	expect(screen.getByText('Tarif unvollständig')).toBeInTheDocument();
	expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('zeigt ohne Preis einen Strich und den Fehler als Hinweis', () => {
	render(PriceTile, { data: undefined, error: new ApiError(0, 'Keine Verbindung zum Server') });
	expect(screen.getByTestId('price-now')).toHaveTextContent('–');
	expect(screen.getByText('Keine Verbindung zum Server')).toBeInTheDocument();
});
