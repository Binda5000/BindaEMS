import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { LoadForecast } from '$lib/api/schemas';
import LoadForecastCard from './LoadForecastCard.svelte';

const data: LoadForecast = {
	issued_at: '2026-10-09T08:00:00+00:00',
	slots: [],
	days: [
		{ date: '2026-10-09', kwh: 12.34 },
		{ date: '2026-10-10', kwh: 14 },
		{ date: '2026-10-11', kwh: null }
	],
	basis: { ledger: 480, ha: 120 },
	status: {
		name: 'load-forecast',
		ok: true,
		message: 'Profil aus 1680 Viertelstunden',
		since: null,
		details: {}
	}
};

it('zeigt Stand, Datenbasis und die Energie der drei Tage', () => {
	render(LoadForecastCard, { data, error: null, today: '2026-10-09' });
	expect(screen.getByText('Lastprognose')).toBeTruthy();
	for (const label of ['Stand', 'Datenbasis', 'Heute', 'Morgen', 'Übermorgen']) {
		expect(screen.getByText(label)).toBeTruthy();
	}
	expect(screen.getByText('09.10.2026, 10:00')).toBeTruthy();
	expect(screen.getByText('480 Viertelstunden aus BindaEMS, 120 aus Home Assistant')).toBeTruthy();
	expect(screen.getByText('12,3 kWh')).toBeTruthy();
	expect(screen.getByText('14,0 kWh')).toBeTruthy();
	expect(screen.getByText('–')).toBeTruthy();
	expect(screen.getByText('Profil aus 1680 Viertelstunden')).toBeTruthy();
});

it('zeigt einen Fehler der Abfrage', () => {
	render(LoadForecastCard, {
		data: undefined,
		error: { detail: 'Nicht erreichbar' } as never,
		today: '2026-10-09'
	});
	expect(screen.getByText('Nicht erreichbar')).toBeTruthy();
});
