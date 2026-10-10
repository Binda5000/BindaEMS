import { fireEvent, render, screen } from '@testing-library/svelte';
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

const next = (ct: number[]): PriceSlot[] =>
	ct.map((value, index) => ({
		...now,
		start: new Date(Date.parse(now.start) + index * 900_000).toISOString(),
		import_gross_ct: value
	}));

it('zeigt beim Überfahren und Antippen den Preis der Viertelstunde', async () => {
	render(PriceNow, { data: { now, next_3h: next([10, 20, 30]) }, error: null });
	expect(screen.queryByTestId('price-tip')).not.toBeInTheDocument();
	const bars = screen.getAllByRole('listitem');
	await fireEvent.pointerEnter(bars[1]);
	expect(screen.getByTestId('price-tip')).toHaveTextContent(/^10:15–10:30\s*20,00\sct\/kWh$/);
	await fireEvent.pointerLeave(screen.getByRole('list'), { pointerType: 'mouse' });
	expect(screen.queryByTestId('price-tip')).not.toBeInTheDocument();
	await fireEvent.pointerDown(bars[2]);
	await fireEvent.pointerLeave(screen.getByRole('list'), { pointerType: 'touch' });
	expect(screen.getByTestId('price-tip')).toHaveTextContent('30,00 ct/kWh'); // bleibt nach dem Tippen
});

it('zeigt den Preis auch mit den Pfeiltasten', async () => {
	render(PriceNow, { data: { now, next_3h: next([10, 20, 30]) }, error: null });
	const list = screen.getByRole('list');
	await fireEvent.keyDown(list, { key: 'ArrowRight' });
	expect(screen.getByTestId('price-tip')).toHaveTextContent('10,00 ct/kWh');
	await fireEvent.keyDown(list, { key: 'ArrowRight' });
	await fireEvent.keyDown(list, { key: 'ArrowRight' });
	await fireEvent.keyDown(list, { key: 'ArrowRight' });
	expect(screen.getByTestId('price-tip')).toHaveTextContent('30,00 ct/kWh');
	await fireEvent.keyDown(list, { key: 'Escape' });
	expect(screen.queryByTestId('price-tip')).not.toBeInTheDocument();
});
