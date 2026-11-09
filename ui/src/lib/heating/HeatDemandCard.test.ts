import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import { heatDemand } from '$lib/testing/heating';
import HeatDemandCard from './HeatDemandCard.svelte';

const { api } = vi.hoisted(() => ({ api: { heatDemand: vi.fn() } }));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

beforeEach(() => {
	api.heatDemand.mockReset();
});
afterEach(() => {
	vi.restoreAllMocks();
	vi.useRealTimers();
});

it('zeigt Saison, Ergebnis der Anpassung, Summen und Klimaanlage je Monat', async () => {
	api.heatDemand.mockResolvedValue(heatDemand());
	render(HeatDemandCard, { day: '2026-10-24' });
	expect(await screen.findByText('Heizsaison 2026/27')).toBeInTheDocument();
	expect(api.heatDemand).toHaveBeenCalledWith('2026-10-24', expect.anything());
	expect(screen.getByRole('heading', { name: 'Wärmebedarf', level: 2 })).toBeInTheDocument();
	expect(
		screen.getByText('Grundbedarf 32,5 kWh je Tag (vor allem Warmwasser)')
	).toBeInTheDocument();
	expect(screen.getByText('Dazu 8,5 kWh je Heizgradtag')).toBeInTheDocument();
	expect(screen.getByText('R² 0,91 aus 3 Tagen')).toBeInTheDocument();
	expect(screen.queryByText(/Ohne Grundbedarf gerechnet/)).not.toBeInTheDocument();
	expect(
		screen.getByText(
			'Bisher in dieser Saison: 2 771,3 kWh Wärme, 722,5 l Öl (794,75 €), 310,4 Heizgradtage'
		)
	).toBeInTheDocument();
	const table = screen.getByRole('table', { name: 'Klimaanlage heizt, Strom je Monat' });
	expect(table).toHaveTextContent('Okt. 2026');
	expect(table).toHaveTextContent('12,5 kWh');
	expect(table).toHaveTextContent('40,3 kWh');
	expect(
		screen.getByText(
			'Die Wärme aus Öl ist Brennerlaufzeit × Nennleistung; Bereitschaftsverluste des Kessels sind darin enthalten. Die Wärme der Klimaanlage ist Strom × 3 (Schätzung).'
		)
	).toBeInTheDocument();
});

it('nennt bei der Gerade durch den Ursprung, dass kein Grundbedarf gerechnet wurde', async () => {
	api.heatDemand.mockResolvedValue(
		heatDemand({ fit: { per_kd: 9, base_kwh_day: 0, r2: 0.8, n: 20, through_origin: true } })
	);
	render(HeatDemandCard, { day: '2026-10-24' });
	expect(
		await screen.findByText('Ohne Grundbedarf gerechnet (er wäre negativ).')
	).toBeInTheDocument();
});

it('zeigt ohne Ergebnis den Grund der API', async () => {
	api.heatDemand.mockResolvedValue(
		heatDemand({ fit: null, reason: 'Noch zu wenige Tage (3 von 14).' })
	);
	render(HeatDemandCard, { day: '2026-10-24' });
	expect(await screen.findByText('Noch zu wenige Tage (3 von 14).')).toBeInTheDocument();
	expect(screen.queryByText(/Grundbedarf/)).not.toBeInTheDocument();
});

it('fehlt ganz bei 404 (kein Brennerstatus)', async () => {
	vi.useFakeTimers();
	api.heatDemand.mockRejectedValue(new ApiError(404, 'Kein Brennerstatus eingerichtet.'));
	const { container } = render(HeatDemandCard, { day: '2026-10-24' });
	await vi.advanceTimersByTimeAsync(0);
	expect(api.heatDemand).toHaveBeenCalledTimes(1);
	expect(container.querySelector('section')).toBeNull();
});

it('lädt bei einem Tag derselben Saison nicht neu, bei einer anderen schon', async () => {
	api.heatDemand.mockResolvedValue(heatDemand());
	const { rerender } = render(HeatDemandCard, { day: '2026-10-24' });
	await screen.findByText('Heizsaison 2026/27');
	await rerender({ day: '2026-11-02' });
	await rerender({ day: '2027-03-01' });
	expect(api.heatDemand).toHaveBeenCalledTimes(1);
	await rerender({ day: '2027-07-01' });
	await waitFor(() => expect(api.heatDemand).toHaveBeenCalledTimes(2));
});

it('speichert die CSV unter dem Namen der Saison', async () => {
	api.heatDemand.mockResolvedValue(heatDemand());
	const blobs: Blob[] = [];
	URL.createObjectURL = vi.fn((blob: Blob) => (blobs.push(blob), 'blob:x'));
	URL.revokeObjectURL = vi.fn();
	const names: string[] = [];
	vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
		this: HTMLAnchorElement
	) {
		names.push(this.download);
	});
	render(HeatDemandCard, { day: '2026-10-24' });
	await fireEvent.click(await screen.findByRole('button', { name: 'CSV herunterladen' }));
	expect(names).toEqual(['waermebedarf-2026-27.csv']);
	expect(await blobs[0].text()).toContain('Tag;Brennerstunden');
	Reflect.deleteProperty(URL, 'createObjectURL');
	Reflect.deleteProperty(URL, 'revokeObjectURL');
});
