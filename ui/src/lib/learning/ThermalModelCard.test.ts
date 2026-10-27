import { fireEvent, render, screen, within } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { ThermalModeStatus } from '$lib/api/schemas';
import { thermal } from '$lib/testing/fixtures';
import ThermalModelCard from './ThermalModelCard.svelte';

type Props = ComponentProps<typeof ThermalModelCard>;

function show(changes: Partial<Props> = {}) {
	const props: Props = {
		data: thermal(),
		error: null,
		admin: true,
		onRecompute: vi.fn(),
		...changes
	};
	render(ThermalModelCard, props);
	return props;
}

/** Die Fakten einer Betriebsart, benannt nach ihrem Kopf */
function facts(head: string): Record<string, string> {
	const block = screen.getByRole('heading', { name: head }).closest('div');
	if (!block) throw new Error(`Betriebsart ${head} fehlt`);
	const terms = within(block).getAllByRole('term');
	return Object.fromEntries(
		terms.map((term) => [
			term.textContent?.trim() ?? '',
			term.nextElementSibling?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
		])
	);
}

it('zeigt je Betriebsart Zeitkonstante und Speicher', () => {
	show();
	expect(screen.getByRole('heading', { name: 'Wärmemodell', level: 2 })).toBeVisible();
	expect(screen.getByRole('heading', { name: 'Heizen · 21 Tage' })).toBeVisible();
	expect(screen.getByRole('heading', { name: 'Kühlen · 6 Tage' })).toBeVisible();
	expect(facts('Heizen · 21 Tage')).toEqual({
		Grundleistung: '82 W je K Temperaturdifferenz',
		Zeitkonstante: '38 h',
		Speicher: '2,4 kWh je K (aus 4 Versatz-Ereignissen, Modell 3,1)',
		'Auskühl-Phasen': '9',
		Anpassung: 'R² 0,81'
	});
	expect(facts('Kühlen · 6 Tage')).toMatchObject({ Zeitkonstante: '–', Speicher: '–' });
	expect(screen.getByText('Modell taugt.')).toBeVisible();
	expect(screen.getByText('Berechnet am 09.10. 03:50.')).toBeVisible();
});

it('nennt den Grund, wenn das Modell nicht taugt', () => {
	const data = thermal();
	const heating = data.modes[0]!;
	const few: ThermalModeStatus = {
		...heating,
		model: { ...heating.model!, days: 9, usable: false, reason: 'few_days' },
		usable: false,
		reason: 'few_days',
		text: 'Noch zu wenig Tage mit Heizen: 9 von 14.'
	};
	show({ data: { ...data, modes: [few, data.modes[1]!] } });
	expect(screen.getByRole('heading', { name: 'Heizen · 9 Tage' })).toBeVisible();
	expect(screen.getByText('Noch zu wenig Tage mit Heizen: 9 von 14.')).toBeVisible();
	expect(screen.getByText('Noch zu wenig Tage mit Kühlen: 6 von 14.')).toBeVisible();
	expect(screen.queryByText('Modell taugt.')).toBeNull();
});

it('startet die Berechnung über die Taste, nur für Admins', async () => {
	const props = show();
	await fireEvent.click(screen.getByRole('button', { name: 'Jetzt neu berechnen' }));
	expect(props.onRecompute).toHaveBeenCalledTimes(1);
});

it('sperrt die Taste, solange eine Berechnung läuft', () => {
	show({ data: { ...thermal(), running: true } });
	expect(screen.getByRole('button', { name: 'Berechnung läuft …' })).toBeDisabled();
});

it('zeigt Nicht-Admins keine Taste, aber den Lauf', () => {
	show({ admin: false, data: { ...thermal(), running: true } });
	expect(screen.queryByRole('button')).toBeNull();
	expect(screen.getByText('Berechnung läuft …')).toBeVisible();
});

it('zeigt einen gestörten Dienst und Fehler der Taste', () => {
	show({
		data: {
			...thermal(),
			status: { ok: false, text: 'Wärmemodell fehlgeschlagen: kaputt' }
		},
		message: 'Nicht erlaubt'
	});
	expect(screen.getByText('Wärmemodell fehlgeschlagen: kaputt')).toBeVisible();
	expect(screen.getByText('Nicht erlaubt')).toBeVisible();
});

it('zeigt ohne Klimaanlage (404) nichts', () => {
	show({ data: undefined, error: new ApiError(404, 'Keine Klimaanlage eingerichtet.') });
	expect(screen.queryByRole('heading', { name: 'Wärmemodell' })).toBeNull();
});

it('zeigt andere Fehler in der Karte', () => {
	show({ data: undefined, error: new ApiError(500, 'Serverfehler') });
	expect(screen.getByRole('heading', { name: 'Wärmemodell' })).toBeVisible();
	expect(screen.getByText('Serverfehler')).toBeVisible();
});
