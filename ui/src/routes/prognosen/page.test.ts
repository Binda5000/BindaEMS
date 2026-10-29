import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { LearningStatus, Role } from '$lib/api/schemas';
import {
	driving,
	drivingVehicle,
	learningStatus,
	quality,
	qualityDaily,
	settingsCurrent,
	thermal
} from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, user } = vi.hoisted(() => ({
	api: {
		learningStatus: vi.fn(),
		trainLoadModel: vi.fn(),
		forecastQuality: vi.fn(),
		forecastQualityDaily: vi.fn(),
		drivingStatus: vi.fn(),
		recomputeDriving: vi.fn(),
		thermalStatus: vi.fn(),
		recomputeThermal: vi.fn(),
		settings: vi.fn()
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
	api.drivingStatus.mockResolvedValue(driving());
	api.thermalStatus.mockResolvedValue(thermal());
	api.settings.mockResolvedValue(settingsCurrent());
});

afterEach(() => {
	vi.useRealTimers();
});

it('zeigt alle acht Karten', async () => {
	render(Page);
	await screen.findByRole('heading', { name: 'Wärmemodell' });
	for (const name of [
		'Lastprognose',
		'Prognosegüte',
		'Fehler je Tag',
		'Backtest',
		'Was das Modell nutzt',
		'Fahrverhalten',
		'Anwesenheit',
		'Wärmemodell'
	]) {
		expect(screen.getByRole('heading', { name })).toBeInTheDocument();
	}
	await waitFor(() =>
		expect(within(card('Was das Modell nutzt')).getByText('Temperatur')).toBeInTheDocument()
	);
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
	expect(
		within(card('Was das Modell nutzt')).getByText('Noch kein Modell trainiert.')
	).toBeInTheDocument();
});

it('zeigt die Güte in der Reihenfolge der Arten mit Verbesserung nur beim Modell', async () => {
	render(Page);
	const table = (await within(card('Prognosegüte')).findByText('Alle Werte')).closest('details')!;
	await within(table).findAllByText('Last Modell');
	for (const name of [
		'Art',
		'Vorlauf',
		'Viertelstunden',
		'MAE',
		'Bias',
		'rel. MAE',
		'besser als Profil'
	]) {
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

it('zeigt die Ablehnung mit 409 als Hinweis, nicht als Fehler', async () => {
	api.trainLoadModel.mockRejectedValue(new ApiError(409, 'Training läuft schon.'));
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Jetzt neu trainieren' }));
	const notice = (await screen.findByText('Training läuft schon.')).closest('.notice');
	expect(notice).toHaveClass('info');
	expect(notice).toHaveTextContent('Hinweis:');
});

it('zeigt andere Fehler beim Start des Trainings als Fehler', async () => {
	api.trainLoadModel.mockRejectedValue(new ApiError(500, 'Start fehlgeschlagen'));
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Jetzt neu trainieren' }));
	const notice = (await screen.findByText('Start fehlgeschlagen')).closest('.notice');
	expect(notice).toHaveClass('error');
});

it('ruht mit der Abfrage im Takt, solange der Tab verborgen ist', async () => {
	api.learningStatus.mockResolvedValue(learningStatus({ training: training(true) }));
	api.drivingStatus.mockResolvedValue(driving({ running: true }));
	const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
	try {
		render(Page);
		await vi.advanceTimersByTimeAsync(0);
		expect(api.learningStatus).toHaveBeenCalledTimes(1);
		expect(api.drivingStatus).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(20_000);
		expect(api.learningStatus).toHaveBeenCalledTimes(1);
		expect(api.drivingStatus).toHaveBeenCalledTimes(1);
		// sichtbar: die Abfrage im Takt läuft weiter
		visibility.mockReturnValue('visible');
		await vi.advanceTimersByTimeAsync(5000);
		expect(api.learningStatus.mock.calls.length).toBeGreaterThan(1);
		expect(api.drivingStatus.mock.calls.length).toBeGreaterThan(1);
	} finally {
		visibility.mockRestore();
	}
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
	const features = card('Was das Modell nutzt');
	await within(features).findByText('Temperatur');
	const labels = within(features)
		.getAllByRole('listitem')
		.map((item) => item.querySelector('.name')?.textContent);
	expect(labels.slice(0, 3)).toEqual(['Temperatur', 'Profil', 'Last vor einem Tag']);
	expect(within(features).getByText('28,0 %')).toBeInTheDocument();
	expect(
		within(features).getByText('Wohnzimmer temperatur (Sensor): nicht im Modell')
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

it('zeigt das Fahrverhalten beider Autos und die Anwesenheit', async () => {
	render(Page);
	const driving = card('Fahrverhalten');
	expect(
		await within(driving).findByRole('heading', {
			name: 'Tesla Model 3 · Quelle: Tessie · 56 Tage'
		})
	).toBeVisible();
	expect(
		within(driving).getByRole('heading', { name: 'e-Golf · Quelle: nur Ansteckvorgänge · 4 Tage' })
	).toBeVisible();
	expect(within(driving).getByText('Fahrverhalten berechnet am 09.10. 03:40.')).toBeVisible();
	expect(
		within(card('Anwesenheit')).getByText('Wahrscheinlichkeit angesteckt, nächste 48 Stunden')
	).toBeVisible();
});

it('nennt bei der Quelle Home Assistant die Entität aus den Einstellungen', async () => {
	const current = settingsCurrent();
	api.settings.mockResolvedValue({
		...current,
		settings: {
			...current.settings,
			learning: {
				...current.settings.learning,
				driving: {
					...current.settings.learning.driving!,
					presence_entities: { tesla: 'device_tracker.tesla' }
				}
			}
		}
	});
	api.drivingStatus.mockResolvedValue(
		driving({ vehicles: [drivingVehicle('tesla', { source: 'ha' })] })
	);
	render(Page);
	expect(
		await within(card('Fahrverhalten')).findByRole('heading', {
			name: 'Tesla Model 3 · Quelle: Home Assistant (device_tracker.tesla) · 56 Tage'
		})
	).toBeVisible();
});

it('zeigt die Quelle Home Assistant auch ohne lesbare Einstellungen', async () => {
	api.settings.mockRejectedValue(new ApiError(403, 'Keine Berechtigung'));
	api.drivingStatus.mockResolvedValue(
		driving({ vehicles: [drivingVehicle('tesla', { source: 'ha' })] })
	);
	render(Page);
	expect(
		await within(card('Fahrverhalten')).findByRole('heading', {
			name: 'Tesla Model 3 · Quelle: Home Assistant · 56 Tage'
		})
	).toBeVisible();
});

it('Taste „Jetzt neu berechnen“ startet die Berechnung und sperrt sich während des Laufs', async () => {
	api.recomputeDriving.mockResolvedValue({ started: true });
	api.drivingStatus.mockResolvedValueOnce(driving()).mockResolvedValue(driving({ running: true }));
	render(Page);
	const button = await within(card('Fahrverhalten')).findByRole('button', {
		name: 'Jetzt neu berechnen'
	});
	await fireEvent.click(button);
	expect(api.recomputeDriving).toHaveBeenCalledTimes(1);
	const running = await within(card('Fahrverhalten')).findByRole('button', {
		name: 'Berechnung läuft …'
	});
	expect(running).toBeDisabled();
});

it('zeigt nach einer Ablehnung mit 409 „Berechnung läuft …“', async () => {
	api.recomputeDriving.mockRejectedValue(new ApiError(409, 'Die Berechnung läuft schon.'));
	api.drivingStatus.mockResolvedValueOnce(driving()).mockResolvedValue(driving({ running: true }));
	render(Page);
	await fireEvent.click(
		await within(card('Fahrverhalten')).findByRole('button', { name: 'Jetzt neu berechnen' })
	);
	expect(
		await within(card('Fahrverhalten')).findByRole('button', { name: 'Berechnung läuft …' })
	).toBeDisabled();
	// kein Fehlerkasten: ein Lauf, der schon läuft, ist kein Fehler
	expect(within(card('Fahrverhalten')).queryByText('Die Berechnung läuft schon.')).toBeNull();
});

it('zeigt andere Fehler beim Start an der Karte', async () => {
	api.recomputeDriving.mockRejectedValue(new ApiError(500, 'Start fehlgeschlagen'));
	render(Page);
	await fireEvent.click(
		await within(card('Fahrverhalten')).findByRole('button', { name: 'Jetzt neu berechnen' })
	);
	expect(await within(card('Fahrverhalten')).findByText('Start fehlgeschlagen')).toBeVisible();
});

it('Taste „Jetzt neu berechnen“ ist für Nicht-Admins nicht da', async () => {
	user.role = 'operator';
	render(Page);
	await within(card('Fahrverhalten')).findByText('Fahrverhalten berechnet am 09.10. 03:40.');
	expect(screen.queryByRole('button', { name: 'Jetzt neu berechnen' })).toBeNull();
});

it('fragt den Stand alle 5 s ab, solange die Berechnung läuft', async () => {
	api.drivingStatus
		.mockResolvedValueOnce(driving({ running: true }))
		.mockResolvedValueOnce(driving({ running: true }))
		.mockResolvedValue(driving());
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.drivingStatus).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(5000);
	expect(api.drivingStatus).toHaveBeenCalledTimes(2);
	await vi.advanceTimersByTimeAsync(5000);
	expect(api.drivingStatus).toHaveBeenCalledTimes(3);
	await vi.advanceTimersByTimeAsync(30_000);
	expect(api.drivingStatus).toHaveBeenCalledTimes(3);
});

it('zeigt Fehler der Abfrage des Fahrverhaltens an den beiden Karten', async () => {
	api.drivingStatus.mockRejectedValue(new ApiError(500, 'Fahrverhalten nicht lesbar'));
	render(Page);
	expect(
		await within(card('Fahrverhalten')).findByText('Fahrverhalten nicht lesbar')
	).toBeVisible();
	expect(within(card('Anwesenheit')).getByText('Fahrverhalten nicht lesbar')).toBeVisible();
});

describe('Wärmemodell', () => {
	it('zeigt je Betriebsart Kopf und Zustand', async () => {
		render(Page);
		const model = await screen.findByRole('heading', { name: 'Wärmemodell' });
		const section = within(model.closest('section')!);
		expect(section.getByRole('heading', { name: 'Heizen · 21 Tage' })).toBeVisible();
		expect(section.getByText('Modell taugt.')).toBeVisible();
		expect(section.getByText('Noch zu wenig Tage mit Kühlen: 6 von 14.')).toBeVisible();
	});

	it('fehlt ohne Klimaanlage (404)', async () => {
		api.thermalStatus.mockRejectedValue(new ApiError(404, 'Keine Klimaanlage eingerichtet.'));
		render(Page);
		await screen.findByRole('heading', { name: 'Fahrverhalten' });
		await vi.advanceTimersByTimeAsync(0);
		expect(api.thermalStatus).toHaveBeenCalledTimes(1);
		expect(screen.queryByRole('heading', { name: 'Wärmemodell' })).toBeNull();
		expect(screen.queryByText('Keine Klimaanlage eingerichtet.')).toBeNull();
	});

	it('fragt nach einem 404 nicht mehr nach', async () => {
		api.thermalStatus.mockRejectedValue(new ApiError(404, 'Keine Klimaanlage eingerichtet.'));
		render(Page);
		await vi.advanceTimersByTimeAsync(0);
		expect(api.thermalStatus).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(10 * 30_000);
		expect(api.thermalStatus).toHaveBeenCalledTimes(1);
	});

	it('fragt nach anderen Fehlern alle 30 s erneut', async () => {
		api.thermalStatus.mockRejectedValue(new ApiError(500, 'Serverfehler'));
		render(Page);
		await vi.advanceTimersByTimeAsync(0);
		expect(api.thermalStatus).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(30_000);
		expect(api.thermalStatus).toHaveBeenCalledTimes(2);
		expect(screen.getByText('Serverfehler')).toBeVisible();
	});

	it('zeigt die Karte erst mit der ersten Antwort', async () => {
		let answer: (value: ReturnType<typeof thermal>) => void = () => {};
		api.thermalStatus.mockReturnValue(new Promise((resolve) => (answer = resolve)));
		render(Page);
		await screen.findByRole('heading', { name: 'Fahrverhalten' });
		expect(screen.queryByRole('heading', { name: 'Wärmemodell' })).toBeNull();
		answer(thermal());
		expect(await screen.findByRole('heading', { name: 'Wärmemodell' })).toBeVisible();
	});

	it('startet die Berechnung und zeigt bei 409 den Lauf ohne Fehler', async () => {
		api.recomputeThermal.mockRejectedValue(new ApiError(409, 'Die Berechnung läuft schon.'));
		api.thermalStatus
			.mockResolvedValueOnce(thermal())
			.mockResolvedValue(thermal({ running: true }));
		render(Page);
		await screen.findByRole('heading', { name: 'Wärmemodell' });
		await fireEvent.click(
			within(card('Wärmemodell')).getByRole('button', { name: 'Jetzt neu berechnen' })
		);
		expect(api.recomputeThermal).toHaveBeenCalledTimes(1);
		expect(
			await within(card('Wärmemodell')).findByRole('button', { name: 'Berechnung läuft …' })
		).toBeDisabled();
		expect(within(card('Wärmemodell')).queryByText('Die Berechnung läuft schon.')).toBeNull();
	});

	it('zeigt andere Fehler beim Start an der Karte', async () => {
		api.recomputeThermal.mockRejectedValue(new ApiError(500, 'Start fehlgeschlagen'));
		render(Page);
		await screen.findByRole('heading', { name: 'Wärmemodell' });
		await fireEvent.click(
			within(card('Wärmemodell')).getByRole('button', { name: 'Jetzt neu berechnen' })
		);
		expect(await within(card('Wärmemodell')).findByText('Start fehlgeschlagen')).toBeVisible();
	});
});
