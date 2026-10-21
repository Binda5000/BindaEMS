import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import { driving, drivingVehicle } from '$lib/testing/fixtures';
import PresenceChart from './PresenceChart.svelte';

// ECharts wird nicht geladen: das Diagramm bleibt leer, die Karte darum herum ist das Thema
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

it('zeigt die Karte „Anwesenheit“ mit Überschrift des Diagramms', () => {
	render(PresenceChart, { data: driving(), error: null });
	expect(screen.getByRole('heading', { name: 'Anwesenheit', level: 2 })).toBeVisible();
	expect(screen.getByText('Wahrscheinlichkeit angesteckt, nächste 48 Stunden')).toBeVisible();
	expect(
		screen.getByRole('img', { name: 'Wahrscheinlichkeit angesteckt, nächste 48 Stunden' })
	).toBeVisible();
});

it('zeichnet nichts, solange kein Auto eine Prognose hat', () => {
	const data = driving({
		vehicles: [drivingVehicle('tesla', { presence: [] }), drivingVehicle('egolf', { presence: [] })]
	});
	render(PresenceChart, { data, error: null });
	expect(screen.queryByRole('img')).toBeNull();
	expect(screen.getByText('Noch keine Daten.')).toBeVisible();
});

it('zeigt den Fehler der Abfrage und „Lädt …“', () => {
	const { unmount } = render(PresenceChart, {
		data: undefined,
		error: new ApiError(500, 'kaputt')
	});
	expect(screen.getByText('kaputt')).toBeVisible();
	unmount();
	render(PresenceChart, { data: undefined, error: null });
	expect(screen.getByText('Lädt …')).toBeVisible();
});
