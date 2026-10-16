import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
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
	render(LoadForecastCard, { data, error: null });
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
		error: { detail: 'Nicht erreichbar' } as never
	});
	expect(screen.getByText('Nicht erreichbar')).toBeTruthy();
});

it('beschriftet die Tage nach der Stelle, nicht nach der Uhr des Browsers', () => {
	// Die Seite war über Mitternacht offen: der Server meldet schon den Folgetag als „heute“.
	vi.useFakeTimers({ toFake: ['Date'] });
	vi.setSystemTime(new Date('2026-10-09T21:59:00Z')); // 23:59 in Wien
	try {
		const { rerender } = render(LoadForecastCard, { data, error: null });
		expect(following('Heute')).toBe('12,3 kWh');
		vi.setSystemTime(new Date('2026-10-09T22:01:00Z')); // 00:01 am 10. 10.
		rerender({
			data: {
				...data,
				days: [
					{ date: '2026-10-10', kwh: 14 },
					{ date: '2026-10-11', kwh: 9 },
					{ date: '2026-10-12', kwh: 7 }
				]
			},
			error: null
		});
		expect(following('Heute')).toBe('14,0 kWh');
		expect(following('Morgen')).toBe('9,00 kWh');
		expect(following('Übermorgen')).toBe('7,00 kWh');
	} finally {
		vi.useRealTimers();
	}
});

function following(label: string): string | null {
	const text = screen.getByText(label, { selector: 'dt' }).nextElementSibling?.textContent;
	return text?.replace(/\s/g, ' ') ?? null;
}
