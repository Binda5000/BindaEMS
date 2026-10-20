import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { LearningStatus, Role } from '$lib/api/schemas';
import { learningStatus, quality, qualityDaily } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, user } = vi.hoisted(() => ({
	api: {
		learningStatus: vi.fn(),
		trainLoadModel: vi.fn(),
		forecastQuality: vi.fn(),
		forecastQualityDaily: vi.fn()
	},
	user: { role: 'admin' as Role }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));
vi.mock('$app/state', () => ({
	page: {
		data: {
			get user() {
				return { id: 1, username: 'test', role: user.role };
			}
		}
	}
}));

function training(running: boolean, extra: Partial<LearningStatus['training']> = {}) {
	return { ...learningStatus().training, running, ...extra };
}

/** Die Karte mit dieser Überschrift */
function card(name: string): HTMLElement {
	const section = screen.getByRole('heading', { name }).closest('section');
	if (!section) throw new Error(`Karte ${name} fehlt`);
	return section;
}

beforeEach(() => {
	vi.useFakeTimers({
		toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout']
	});
	vi.setSystemTime(new Date('2026-10-09T08:00:00Z'));
	user.role = 'admin';
	vi.resetAllMocks();
	api.learningStatus.mockResolvedValue(learningStatus());
	api.forecastQuality.mockImplementation(async (days: number) => quality({ days }));
	api.forecastQualityDaily.mockImplementation(async (days: number) => qualityDaily({ days }));
});

afterEach(() => {
	vi.useRealTimers();
});

it('zeigt alle fünf Karten', async () => {
	render(Page);
	for (const name of ['Lastprognose', 'Prognosegüte', 'Fehler je Tag', 'Backtest', 'Merkmale']) {
		expect(screen.getByRole('heading', { name })).toBeInTheDocument();
	}
	await waitFor(() => expect(within(card('Merkmale')).getByText('Temperatur')).toBeInTheDocument());
});

it('zeigt Verfahren und Grund der Aktivierung', async () => {
	render(Page);
	const model = card('Lastprognose');
	expect(await within(model).findByText('Verfahren: Modell')).toBeInTheDocument();
	expect(
		within(model).getByText(
			'Modell aktiv: in den letzten 14 Tagen 29 % genauer als das Profil (im Schattenbetrieb).'
		)
	).toBeInTheDocument();
	expect(within(model).getByText('Modell vom 09.10.2026 03:15')).toBeInTheDocument();
	expect(within(model).getByText(/Daten 14\.08\.–09\.10\.2026 · .*Viertelstunden/)).toBeVisible();
	expect(
		within(model).getByText('Trainiert am 09.10. 03:15 mit 5376 Viertelstunden.')
	).toBeInTheDocument();
});

it('zeigt das Profil mit dem Grund, wenn es noch kein Modell gibt', async () => {
	const status = learningStatus();
	api.learningStatus.mockResolvedValue(
		learningStatus({
			method: 'profile',
			model: null,
			backtest: null,
			sensors: [],
			activation: {
				...status.activation,
				active: false,
				reason: 'no_model',
				text: 'Profil aktiv: Es gibt noch kein brauchbares Modell.'
			}
		})
	);
	render(Page);
	const model = card('Lastprognose');
	expect(await within(model).findByText('Verfahren: Profil')).toBeInTheDocument();
	expect(
		within(model).getByText('Profil aktiv: Es gibt noch kein brauchbares Modell.')
	).toBeInTheDocument();
	expect(within(model).getByText('Noch kein Modell trainiert.')).toBeInTheDocument();
	expect(within(card('Merkmale')).getByText('Noch kein Modell trainiert.')).toBeInTheDocument();
});

it('zeigt die Güte in der Reihenfolge der Arten mit Verbesserung nur beim Modell', async () => {
	render(Page);
	const table = card('Prognosegüte');
	await within(table).findAllByText('Last Modell');
	for (const name of ['Art', 'Vorlauf', 'Slots', 'MAE', 'Bias', 'rel. MAE', 'besser als Profil']) {
		expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument();
	}
	const kinds = within(table)
		.getAllByRole('row')
		.slice(1)
		.map((row) => row.querySelector('td')?.textContent);
	expect(kinds).toEqual([
		'PV',
		'Last (verwendet)',
		'Last (verwendet)',
		'Last (verwendet)',
		'Last Profil',
		'Last Profil',
		'Last Profil',
		'Last Modell',
		'Last Modell',
		'Last Modell'
	]);
	const better = within(table)
		.getAllByRole('row')
		.slice(1)
		.map((row) => row.querySelector('td:last-child')?.textContent?.replace('\u00a0', ' '));
	expect(better).toEqual(['–', '–', '–', '–', '–', '–', '–', '+28 %', '+29 %', '+29 %']);
});

it('Taste „Jetzt neu trainieren“ startet das Training und sperrt sich während des Laufs', async () => {
	api.trainLoadModel.mockResolvedValue({ started: true });
	api.learningStatus
		.mockResolvedValueOnce(learningStatus())
		.mockResolvedValue(learningStatus({ training: training(true, { outcome: null, text: null }) }));
	render(Page);
	const button = await screen.findByRole('button', { name: 'Jetzt neu trainieren' });
	expect(button).toBeEnabled();
	await fireEvent.click(button);
	expect(api.trainLoadModel).toHaveBeenCalledTimes(1);
	const running = await screen.findByRole('button', { name: 'Training läuft …' });
	expect(running).toBeDisabled();
});

it('Taste „Jetzt neu trainieren“ ist für Nicht-Admins nicht da', async () => {
	user.role = 'operator';
	render(Page);
	await screen.findByText('Verfahren: Modell');
	expect(screen.queryByRole('button', { name: 'Jetzt neu trainieren' })).not.toBeInTheDocument();
});

it('zeigt während eines Laufs „Training läuft …“ auch ohne Taste', async () => {
	user.role = 'viewer';
	api.learningStatus.mockResolvedValue(learningStatus({ training: training(true) }));
	render(Page);
	expect(await screen.findByText('Training läuft …')).toBeInTheDocument();
	expect(screen.queryByRole('button', { name: /Training läuft/ })).not.toBeInTheDocument();
});

it('zeigt, wenn schon ein Training läuft (409)', async () => {
	api.trainLoadModel.mockRejectedValue(new ApiError(409, 'Training läuft schon.'));
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Jetzt neu trainieren' }));
	expect(await screen.findByText('Training läuft schon.')).toBeInTheDocument();
});

it('fragt den Stand alle 5 s ab, solange das Training läuft, und lädt danach die Güte neu', async () => {
	api.learningStatus
		.mockResolvedValueOnce(learningStatus({ training: training(true) }))
		.mockResolvedValueOnce(learningStatus({ training: training(true) }))
		.mockResolvedValue(learningStatus());
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.learningStatus).toHaveBeenCalledTimes(1);
	expect(api.forecastQuality).toHaveBeenCalledTimes(1);

	await vi.advanceTimersByTimeAsync(5000);
	expect(api.learningStatus).toHaveBeenCalledTimes(2);
	await vi.advanceTimersByTimeAsync(5000);
	expect(api.learningStatus).toHaveBeenCalledTimes(3);
	// im Hintergrund, ohne die Sitzung zu verlängern
	expect(api.learningStatus.mock.calls.slice(1).every(([o]) => o.background === true)).toBe(true);
	// fertig: keine Abfrage mehr, aber Güte und Verlauf neu
	await vi.advanceTimersByTimeAsync(30_000);
	expect(api.learningStatus).toHaveBeenCalledTimes(3);
	expect(api.forecastQuality).toHaveBeenCalledTimes(2);
	expect(api.forecastQualityDaily).toHaveBeenCalledTimes(2);
});

it('hört beim Verlassen der Seite auf zu fragen', async () => {
	api.learningStatus.mockResolvedValue(learningStatus({ training: training(true) }));
	const { unmount } = render(Page);
	await vi.advanceTimersByTimeAsync(5000);
	const calls = api.learningStatus.mock.calls.length;
	expect(calls).toBeGreaterThanOrEqual(2);
	unmount();
	await vi.advanceTimersByTimeAsync(20_000);
	expect(api.learningStatus).toHaveBeenCalledTimes(calls);
});

it('zeigt den Hinweis, wenn es noch keinen Backtest gibt', async () => {
	api.learningStatus.mockResolvedValue(learningStatus({ backtest: null }));
	render(Page);
	expect(
		await within(card('Backtest')).findByText(
			'Noch kein Backtest (braucht mindestens 28 Tage Daten).'
		)
	).toBeInTheDocument();
});

it('zeigt den Backtest je Woche mit Gesamt', async () => {
	render(Page);
	const table = card('Backtest');
	await within(table).findByText('Gesamt');
	for (const name of ['Woche ab', 'Vorlauf', 'Slots', 'Modell', 'Profil', 'besser']) {
		expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument();
	}
	expect(within(table).getAllByRole('row')).toHaveLength(1 + 9 + 1);
	expect(within(table).getByText('+17 %')).toBeInTheDocument();
});

it('nennt die Merkmale absteigend und einen Sensor ohne Werte „nicht im Modell“', async () => {
	render(Page);
	const features = card('Merkmale');
	await within(features).findByText('Temperatur');
	const labels = within(features)
		.getAllByRole('row')
		.slice(1)
		.map((row) => row.querySelector('td')?.textContent);
	expect(labels.slice(0, 3)).toEqual(['Temperatur', 'Profil', 'Last vor einem Tag']);
	expect(within(features).getByText('28,0 %')).toBeInTheDocument();
	expect(
		within(features).getByText('sensor.wohnzimmer_temperatur: nicht im Modell')
	).toBeInTheDocument();
});

it('wechselt den Zeitraum 7/14/28/60 Tage für Güte und Verlauf', async () => {
	render(Page);
	await waitFor(() => expect(api.forecastQuality).toHaveBeenCalledWith(14, expect.anything()));
	const switcher = within(card('Prognosegüte'));
	expect(switcher.getByRole('button', { name: '14 Tage' })).toHaveAttribute('aria-pressed', 'true');
	for (const days of [7, 28, 60]) {
		await fireEvent.click(switcher.getByRole('button', { name: `${days} Tage` }));
		await waitFor(() =>
			expect(api.forecastQuality).toHaveBeenLastCalledWith(days, expect.anything())
		);
		expect(api.forecastQualityDaily).toHaveBeenLastCalledWith(days, expect.anything());
		expect(switcher.getByRole('button', { name: `${days} Tage` })).toHaveAttribute(
			'aria-pressed',
			'true'
		);
	}
});

it('zeigt leere Güte', async () => {
	api.forecastQuality.mockResolvedValue(quality({ rows: [] }));
	api.forecastQualityDaily.mockResolvedValue(qualityDaily({ rows: [] }));
	render(Page);
	await waitFor(() =>
		expect(within(card('Prognosegüte')).getByText('Noch keine Werte im Zeitraum.')).toBeVisible()
	);
	expect(within(card('Fehler je Tag')).getByText('Noch keine Werte im Zeitraum.')).toBeVisible();
});

it('zeigt Fehler der Abfragen an der Karte', async () => {
	api.learningStatus.mockRejectedValue(new ApiError(500, 'Lernstand nicht lesbar'));
	api.forecastQuality.mockRejectedValue(new ApiError(500, 'Güte nicht lesbar'));
	render(Page);
	expect(await within(card('Lastprognose')).findByText('Lernstand nicht lesbar')).toBeVisible();
	expect(await within(card('Prognosegüte')).findByText('Güte nicht lesbar')).toBeVisible();
});
