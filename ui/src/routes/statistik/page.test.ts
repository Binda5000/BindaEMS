import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { page } from '$app/state';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { chargingSession, limits, stats } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, goto } = vi.hoisted(() => ({
	api: { stats: vi.fn(), chargingSessions: vi.fn(), limits: vi.fn() },
	goto: vi.fn()
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));
vi.mock('$app/navigation', () => ({ goto }));
// reaktive URL: Tests ändern sie wie die Navigation des Browsers
vi.mock('$app/state', async () => {
	const { SvelteURL } = await import('svelte/reactivity');
	return { page: { url: new SvelteURL('https://ems.lan/statistik') } };
});

/** Die URL der Seite wie nach einer Navigation setzen */
function navigate(search: string) {
	page.url.search = search;
}

beforeEach(() => {
	vi.useFakeTimers({
		toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout']
	});
	vi.setSystemTime(new Date('2026-10-09T08:00:00Z')); // 10:00 in Wien
	vi.resetAllMocks();
	navigate('');
	api.stats.mockResolvedValue(stats());
	api.chargingSessions.mockResolvedValue([chargingSession()]);
	api.limits.mockResolvedValue(limits());
});

afterEach(() => {
	vi.useRealTimers();
});

it('öffnet ohne Parameter mit dem heutigen Tag', async () => {
	render(Page);
	expect(screen.getByRole('heading', { name: 'Fr., 09.10.2026' })).toBeInTheDocument();
	await waitFor(() => expect(api.stats).toHaveBeenCalled());
	expect(api.stats.mock.lastCall?.slice(0, 2)).toEqual(['day', '2026-10-09']);
	expect(screen.getByRole('button', { name: 'Tag' })).toHaveAttribute('aria-pressed', 'true');
});

it('page reads period and date from the URL and reloads on change', async () => {
	navigate('?zeitraum=woche&datum=2026-10-07');
	render(Page);
	expect(screen.getByRole('heading', { name: 'KW 41 · 05.10.–11.10.2026' })).toBeInTheDocument();
	await waitFor(() => expect(api.stats).toHaveBeenCalledTimes(1));
	expect(api.stats.mock.lastCall?.slice(0, 2)).toEqual(['week', '2026-10-07']);

	navigate('?zeitraum=monat&datum=2026-09-15');
	await waitFor(() =>
		expect(api.stats.mock.lastCall?.slice(0, 2)).toEqual(['month', '2026-09-15'])
	);
	expect(screen.getByRole('heading', { name: 'September 2026' })).toBeInTheDocument();
	expect(screen.getByRole('button', { name: 'Monat' })).toHaveAttribute('aria-pressed', 'true');
});

it('fällt bei ungültigen Parametern auf Tag und heute zurück', async () => {
	navigate('?zeitraum=quartal&datum=morgen');
	render(Page);
	await waitFor(() => expect(api.stats).toHaveBeenCalled());
	expect(api.stats.mock.lastCall?.slice(0, 2)).toEqual(['day', '2026-10-09']);
});

it('wählt den Zeitraum und blättert über die URL', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await fireEvent.click(screen.getByRole('button', { name: 'Woche' }));
	expect(goto).toHaveBeenLastCalledWith(
		'/statistik?zeitraum=woche&datum=2026-10-09',
		expect.objectContaining({ keepFocus: true, noScroll: true })
	);
	await fireEvent.click(screen.getByRole('button', { name: 'Zurück' }));
	expect(goto.mock.lastCall?.[0]).toBe('/statistik?zeitraum=monat&datum=2026-09-09');
	expect(screen.getByRole('button', { name: 'Weiter' })).toBeDisabled();
	expect(screen.getByRole('button', { name: 'Heute' })).toBeDisabled();
});

it('blättert in der Vergangenheit vor und springt mit „Heute“ zurück', async () => {
	navigate('?zeitraum=woche&datum=2026-09-30');
	render(Page);
	await fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
	expect(goto.mock.lastCall?.[0]).toBe('/statistik?zeitraum=woche&datum=2026-10-07');
	await fireEvent.click(screen.getByRole('button', { name: 'Heute' }));
	expect(goto.mock.lastCall?.[0]).toBe('/statistik?zeitraum=woche&datum=2026-10-09');
});

it('lädt den laufenden Zeitraum jede Minute neu, einen vergangenen nur einmal', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.stats).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(60_000);
	expect(api.stats).toHaveBeenCalledTimes(2);
	await vi.advanceTimersByTimeAsync(120_000);
	expect(api.stats).toHaveBeenCalledTimes(4);

	api.stats.mockClear();
	navigate('?zeitraum=monat&datum=2026-09-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(api.stats).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(5 * 60_000);
	expect(api.stats).toHaveBeenCalledTimes(1);
});

it('versucht einen vergangenen Zeitraum nach einem Fehler nach 30 s erneut', async () => {
	const { ApiError } = await import('$lib/api/errors');
	navigate('?zeitraum=monat&datum=2026-09-15');
	api.stats.mockRejectedValueOnce(new ApiError(503, 'Statistik nicht verfügbar'));
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.stats).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(29_000);
	expect(api.stats).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(1_000);
	expect(api.stats).toHaveBeenCalledTimes(2);
	await screen.findByText('Autarkie');
	await vi.advanceTimersByTimeAsync(5 * 60_000);
	expect(api.stats).toHaveBeenCalledTimes(2); // gelungen: nicht wieder laden
});

it('zeigt Kacheln, Hinweise, Diagramm und Kosten', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await screen.findByText('Autarkie');
	expect(screen.getAllByTestId('tile')).toHaveLength(5);
	expect(screen.getByText('Abdeckung 95 %')).toBeInTheDocument();
	expect(screen.getByText('2 Viertelstunden geschätzt')).toBeInTheDocument();
	expect(screen.getByText('bis jetzt')).toBeInTheDocument();
	expect(screen.getByRole('img', { name: /Energie/ })).toBeInTheDocument();
	expect(screen.getByRole('heading', { name: 'Energie' })).toBeInTheDocument();
	expect(screen.getByRole('cell', { name: 'Grundgebühren' })).toBeInTheDocument();
});

it('hält Zurück, Weiter, Heute und den Titel in einer Zeile', async () => {
	render(Page);
	const row = screen.getByRole('heading', { name: 'Fr., 09.10.2026' }).parentElement;
	for (const name of ['Zurück', 'Weiter', 'Heute']) {
		expect(screen.getByRole('button', { name }).parentElement).toBe(row);
	}
	// der Zeitraum ist die Segment-Leiste, nicht Teil dieser Zeile
	expect(screen.getByRole('group', { name: 'Zeitraum wählen' })).not.toBe(row);
	expect(row).not.toContainElement(screen.getByRole('button', { name: 'Tag' }));
});

it('zeigt unter den Kacheln die Fußnote', async () => {
	render(Page);
	expect(
		await screen.findByText('* gegenüber Fixtarif ohne Anlage. „Erlös“ ist der Einspeiseerlös.')
	).toBeVisible();
});

it('sagt bei einem vergangenen Zeitraum nicht „bis jetzt“', async () => {
	navigate('?zeitraum=monat&datum=2026-09-15');
	render(Page);
	await screen.findByText('Autarkie');
	expect(screen.queryByText('bis jetzt')).not.toBeInTheDocument();
});

it('zeigt den Fehler der Abfrage', async () => {
	const { ApiError } = await import('$lib/api/errors');
	api.stats.mockRejectedValue(new ApiError(503, 'Statistik nicht verfügbar'));
	render(Page);
	expect(await screen.findByText('Statistik nicht verfügbar')).toBeInTheDocument();
});

it('rückt um Mitternacht auf den neuen Tag weiter', async () => {
	vi.setSystemTime(new Date('2026-10-09T21:59:00Z')); // 23:59 in Wien
	render(Page);
	expect(screen.getByRole('heading', { name: 'Fr., 09.10.2026' })).toBeInTheDocument();
	await vi.advanceTimersByTimeAsync(2 * 60_000);
	expect(screen.getByRole('heading', { name: 'Sa., 10.10.2026' })).toBeInTheDocument();
	expect(api.stats.mock.lastCall?.slice(0, 2)).toEqual(['day', '2026-10-10']);
});

it('zeigt unter den Kosten Autos, Laden je Auto, Ladesessions, Verbraucher, Monatsspitzen und Abgleich', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await screen.findByRole('table', { name: 'Ladesessions' });
	const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
	expect(headings).toEqual([
		'Oktober 2026',
		'Energie',
		'Kosten',
		'Autos',
		'Laden je Auto',
		'Geplantes Laden',
		'Ladesessions',
		'Verbraucher',
		'Monatsspitzen',
		'Abgleich mit den Zählern'
	]);
	expect(screen.getByRole('table', { name: 'Autos' })).toBeInTheDocument();
	// Wallbox-Namen aus den Limits
	expect(await screen.findByRole('cell', { name: 'Wallbox Garage' })).toBeInTheDocument();
	expect(screen.getByRole('cell', { name: 'Wallbox EVCS' })).toBeInTheDocument();
});

it('lässt die Monatsspitzen in Tag und Woche weg', async () => {
	api.stats.mockResolvedValue(stats({ period: 'week' }));
	navigate('?zeitraum=woche&datum=2026-10-07');
	render(Page);
	await screen.findByRole('table', { name: 'Autos' });
	expect(screen.queryByRole('heading', { name: 'Monatsspitzen' })).not.toBeInTheDocument();
});

it('lädt die Sessions des Zeitraums, den laufenden jede Minute, einen vergangenen einmal', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.chargingSessions.mock.lastCall?.slice(0, 3)).toEqual([
		'2026-10-01',
		'2026-10-31',
		undefined
	]);
	expect(api.chargingSessions).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(60_000);
	expect(api.chargingSessions).toHaveBeenCalledTimes(2);

	api.chargingSessions.mockClear();
	navigate('?zeitraum=woche&datum=2026-09-30');
	await vi.advanceTimersByTimeAsync(0);
	expect(api.chargingSessions.mock.lastCall?.slice(0, 2)).toEqual(['2026-09-28', '2026-10-04']);
	await vi.advanceTimersByTimeAsync(5 * 60_000);
	expect(api.chargingSessions).toHaveBeenCalledTimes(1);
});

it('versucht die Sessions eines vergangenen Zeitraums nach einem Fehler nach 30 s erneut', async () => {
	const { ApiError } = await import('$lib/api/errors');
	navigate('?zeitraum=monat&datum=2026-09-15');
	api.chargingSessions.mockRejectedValueOnce(new ApiError(503, 'Sessions nicht verfügbar'));
	render(Page);
	expect(await screen.findByText('Sessions nicht verfügbar')).toBeInTheDocument();
	expect(api.chargingSessions).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(30_000);
	expect(api.chargingSessions).toHaveBeenCalledTimes(2);
	await screen.findByRole('table', { name: 'Ladesessions' });
	// die übrigen Karten blieben vom Fehler unberührt
	expect(screen.getByRole('table', { name: 'Autos' })).toBeInTheDocument();
});

it('zeigt nur beim ersten Laden „Lädt …“', async () => {
	let answer: (value: ReturnType<typeof stats>) => void = () => {};
	api.stats.mockReturnValue(new Promise((resolve) => (answer = resolve)));
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	expect(await screen.findByText('Lädt …')).toBeInTheDocument();
	answer(stats());
	await screen.findByRole('table', { name: 'Ladesessions' });
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();

	api.stats.mockReturnValue(new Promise(() => {}));
	api.chargingSessions.mockReturnValue(new Promise(() => {}));
	navigate('?zeitraum=monat&datum=2026-09-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('heading', { name: 'September 2026' })).toBeInTheDocument();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument(); // beim Blättern nicht
});

it('lässt beim Blättern die alten Werte gedimmt stehen, bis die neuen da sind', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await screen.findByRole('table', { name: 'Ladesessions' });
	const results = () => screen.getByText('Autarkie').closest('[aria-busy]');
	expect(results()).toHaveAttribute('aria-busy', 'false');

	let answerStats: (value: ReturnType<typeof stats>) => void = () => {};
	let answerSessions: (value: ReturnType<typeof chargingSession>[]) => void = () => {};
	api.stats.mockReturnValue(new Promise((resolve) => (answerStats = resolve)));
	api.chargingSessions.mockReturnValue(new Promise((resolve) => (answerSessions = resolve)));
	const hintList = () => screen.getByText(/^Abdeckung/).closest('ul');
	expect(results()).not.toHaveClass('stale');
	expect(hintList()).not.toHaveClass('stale');
	navigate('?zeitraum=monat&datum=2026-09-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('heading', { name: 'September 2026' })).toBeInTheDocument();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();
	expect(results()).toHaveAttribute('aria-busy', 'true');
	expect(results()).toHaveClass('stale');
	expect(hintList()).toHaveClass('stale');
	expect(screen.getByRole('table', { name: 'Ladesessions' })).toBeInTheDocument();

	answerStats(stats());
	await vi.advanceTimersByTimeAsync(0);
	// die Statistik ist da, die Sessions noch nicht: weiter beschäftigt, aber nicht mehr alt
	expect(results()).toHaveAttribute('aria-busy', 'true');
	expect(results()).not.toHaveClass('stale');
	expect(hintList()).not.toHaveClass('stale');
	answerSessions([chargingSession()]);
	await vi.advanceTimersByTimeAsync(0);
	expect(results()).toHaveAttribute('aria-busy', 'false');
});

it('lässt Hinweise und Diagramm-Label beim Blättern bei den gezeigten Werten', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await screen.findByText('bis jetzt');
	expect(screen.getByRole('img', { name: 'Energie Oktober 2026' })).toBeInTheDocument();

	api.stats.mockReturnValue(new Promise(() => {}));
	navigate('?zeitraum=monat&datum=2026-09-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('heading', { name: 'September 2026' })).toBeInTheDocument();
	// gedimmt steht noch der Oktober: laufend, mit seinem Titel im Diagramm
	expect(screen.getByText('bis jetzt')).toBeInTheDocument();
	expect(screen.getByRole('img', { name: 'Energie Oktober 2026' })).toBeInTheDocument();
});

it('mischt beim Blättern keine Sessions eines anderen Zeitraums unter die Werte', async () => {
	api.chargingSessions.mockResolvedValue([
		chargingSession({ vehicle: 'erst', vehicle_name: 'Erstwagen' })
	]);
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('cell', { name: 'Erstwagen' })).toBeInTheDocument();

	// September: die Sessions kommen vor der Statistik, gezeigt bleiben die des Oktobers
	let answerStats: (value: ReturnType<typeof stats>) => void = () => {};
	api.stats.mockReturnValue(new Promise((resolve) => (answerStats = resolve)));
	api.chargingSessions.mockResolvedValue([
		chargingSession({ vehicle: 'zweit', vehicle_name: 'Zweitwagen' })
	]);
	navigate('?zeitraum=monat&datum=2026-09-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('cell', { name: 'Erstwagen' })).toBeInTheDocument();
	expect(screen.queryByRole('cell', { name: 'Zweitwagen' })).not.toBeInTheDocument();
	answerStats(stats());
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('cell', { name: 'Zweitwagen' })).toBeInTheDocument();
	expect(screen.queryByRole('cell', { name: 'Erstwagen' })).not.toBeInTheDocument();

	// August: die Statistik kommt vor den Sessions, die des Septembers stehen nicht darunter
	api.stats.mockResolvedValue(stats());
	api.chargingSessions.mockReturnValue(new Promise(() => {}));
	navigate('?zeitraum=monat&datum=2026-08-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByText('Autarkie').closest('[aria-busy]')).not.toHaveClass('stale');
	expect(screen.queryByRole('cell', { name: 'Zweitwagen' })).not.toBeInTheDocument();
});

it('zeigt beim Blättern einen Fehler statt der alten Werte', async () => {
	const { ApiError } = await import('$lib/api/errors');
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await screen.findByText('Autarkie');
	api.stats.mockRejectedValue(new ApiError(503, 'Statistik nicht verfügbar'));
	navigate('?zeitraum=monat&datum=2026-09-15');
	expect(await screen.findByText('Statistik nicht verfügbar')).toBeInTheDocument();
	expect(screen.queryByText('Autarkie')).not.toBeInTheDocument();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();
});

it('zeigt nach einem Fehler beim Weiterblättern nicht die Werte von davor', async () => {
	const { ApiError } = await import('$lib/api/errors');
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await screen.findByText('Autarkie');
	api.stats.mockRejectedValue(new ApiError(503, 'Statistik nicht verfügbar'));
	navigate('?zeitraum=monat&datum=2026-09-15');
	await screen.findByText('Statistik nicht verfügbar');

	api.stats.mockReturnValue(new Promise(() => {}));
	api.chargingSessions.mockReturnValue(new Promise(() => {}));
	navigate('?zeitraum=monat&datum=2026-08-15');
	await vi.advanceTimersByTimeAsync(0);
	expect(screen.getByRole('heading', { name: 'August 2026' })).toBeInTheDocument();
	expect(screen.queryByText('Autarkie')).not.toBeInTheDocument(); // nicht der Oktober
	expect(screen.queryByText(/^Abdeckung/)).not.toBeInTheDocument();
	expect(screen.getByText('Lädt …')).toBeInTheDocument();
});

it('fragt nach dem Verlassen der Seite nicht mehr nach', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09'); // laufender Zeitraum: Takt von 60 s
	const { unmount } = render(Page);
	await vi.advanceTimersByTimeAsync(60_000);
	expect(api.stats).toHaveBeenCalledTimes(2);
	expect(api.chargingSessions).toHaveBeenCalledTimes(2);
	unmount();
	api.stats.mockClear();
	api.chargingSessions.mockClear();
	api.limits.mockClear();
	await vi.advanceTimersByTimeAsync(10 * 60_000);
	expect(api.stats).not.toHaveBeenCalled();
	expect(api.chargingSessions).not.toHaveBeenCalled();
	expect(api.limits).not.toHaveBeenCalled();
	expect(vi.getTimerCount()).toBe(0);
});

it('filtert die Ladesessions nach dem Auto aus der Adresse', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09&auto=tesla');
	render(Page);
	await waitFor(() => expect(api.chargingSessions).toHaveBeenCalled());
	expect(api.chargingSessions.mock.lastCall?.slice(0, 3)).toEqual([
		'2026-10-01',
		'2026-10-31',
		'tesla'
	]);
	expect(await screen.findByRole('group', { name: 'Auto wählen' })).toBeInTheDocument();
	expect(screen.getByRole('button', { name: 'Tesla Model 3' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
});

it('lädt ohne Auto alle Ladesessions', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await waitFor(() => expect(api.chargingSessions).toHaveBeenCalled());
	expect(api.chargingSessions.mock.lastCall?.[2]).toBeUndefined();
});

it('behält das Auto beim Blättern und entfernt es mit „Alle“', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09&auto=tesla');
	render(Page);
	await fireEvent.click(screen.getByRole('button', { name: 'Zurück' }));
	expect(goto.mock.lastCall?.[0]).toBe('/statistik?zeitraum=monat&datum=2026-09-09&auto=tesla');
	await fireEvent.click(await screen.findByRole('button', { name: 'Alle' }));
	expect(goto).toHaveBeenLastCalledWith(
		'/statistik?zeitraum=monat&datum=2026-10-09',
		expect.objectContaining({ keepFocus: true, noScroll: true })
	);
});

it('wählt ein Auto über die Adresse', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'e-Golf' }));
	expect(goto).toHaveBeenLastCalledWith(
		'/statistik?zeitraum=monat&datum=2026-10-09&auto=egolf',
		expect.objectContaining({ keepFocus: true, noScroll: true })
	);
});

it('lädt beim Wechsel des Autos nur die Sessions neu, nicht die Statistik', async () => {
	navigate('?zeitraum=monat&datum=2026-09-15'); // vergangen: kein Takt
	render(Page);
	await screen.findByRole('table', { name: 'Ladesessions' });
	expect(api.stats).toHaveBeenCalledTimes(1);
	expect(api.chargingSessions).toHaveBeenCalledTimes(1);

	let answerSessions: (value: ReturnType<typeof chargingSession>[]) => void = () => {};
	api.chargingSessions.mockReturnValue(new Promise((resolve) => (answerSessions = resolve)));
	navigate('?zeitraum=monat&datum=2026-09-15&auto=tesla');
	await vi.advanceTimersByTimeAsync(0);
	expect(api.chargingSessions).toHaveBeenCalledTimes(2);
	expect(api.chargingSessions.mock.lastCall?.slice(0, 3)).toEqual([
		'2026-09-01',
		'2026-09-30',
		'tesla'
	]);
	expect(api.stats).toHaveBeenCalledTimes(1);
	// die Werte der Statistik bleiben stehen, ungedimmt; nur die Sessions sind noch unterwegs
	const results = screen.getByText('Autarkie').closest('[aria-busy]');
	expect(results).not.toHaveClass('stale');
	expect(results).toHaveAttribute('aria-busy', 'true');
	expect(screen.getByRole('heading', { name: 'September 2026' })).toBeInTheDocument();
	// die Sessions des vorigen Autos stehen gedimmt da, und das CSV bleibt gesperrt
	const sessionsCard = screen.getByRole('table', { name: 'Ladesessions' }).closest('.sessions');
	expect(sessionsCard).toHaveClass('stale');
	expect(screen.getByRole('button', { name: 'CSV herunterladen' })).toBeDisabled();

	answerSessions([chargingSession({ vehicle: 'tesla', vehicle_name: 'Tesla Model 3' })]);
	await vi.advanceTimersByTimeAsync(0);
	expect(results).toHaveAttribute('aria-busy', 'false');
	expect(screen.getByRole('table', { name: 'Ladesessions' }).closest('.sessions')).not.toHaveClass(
		'stale'
	);
	expect(screen.getByRole('button', { name: 'CSV herunterladen' })).toBeEnabled();
	expect(
		within(screen.getByRole('table', { name: 'Ladesessions' })).getByRole('cell', {
			name: 'Tesla Model 3'
		})
	).toBeInTheDocument();
	expect(api.stats).toHaveBeenCalledTimes(1);
});

it('lässt beim Wechsel des Autos die Seite stehen, wenn die Sessions scheitern', async () => {
	const { ApiError } = await import('$lib/api/errors');
	navigate('?zeitraum=monat&datum=2026-09-15');
	render(Page);
	await screen.findByRole('table', { name: 'Ladesessions' });
	api.chargingSessions.mockRejectedValue(new ApiError(503, 'Sessions nicht verfügbar'));
	navigate('?zeitraum=monat&datum=2026-09-15&auto=tesla');
	expect(await screen.findByText('Sessions nicht verfügbar')).toBeInTheDocument();
	expect(screen.getByText('Autarkie')).toBeInTheDocument(); // Statistik unberührt
	expect(screen.queryByRole('table', { name: 'Ladesessions' })).not.toBeInTheDocument();
	expect(api.stats).toHaveBeenCalledTimes(1);
});

it('benennt das Diagramm „Laden je Auto“ mit dem Titel des gezeigten Zeitraums', async () => {
	navigate('?zeitraum=monat&datum=2026-10-09');
	render(Page);
	expect(
		await screen.findByRole('img', { name: 'Laden je Auto Oktober 2026' })
	).toBeInTheDocument();
});

it('benennt die CSV nach Zeitraum und Auto der gezeigten Sessions', async () => {
	Object.assign(URL, { createObjectURL: () => 'blob:csv', revokeObjectURL: () => {} });
	const downloads: string[] = [];
	const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
		this: HTMLAnchorElement
	) {
		downloads.push(this.download);
	});
	try {
		navigate('?zeitraum=monat&datum=2026-10-09&auto=tesla');
		render(Page);
		await fireEvent.click(await screen.findByRole('button', { name: 'CSV herunterladen' }));
		expect(downloads).toEqual(['ladesessions-2026-10-01-2026-10-31-tesla.csv']);

		// September noch unterwegs: die Sessions des Oktobers stehen gedimmt da, das CSV ist gesperrt
		api.stats.mockReturnValue(new Promise(() => {}));
		navigate('?zeitraum=monat&datum=2026-09-15&auto=tesla');
		await vi.advanceTimersByTimeAsync(0);
		expect(screen.getByRole('button', { name: 'CSV herunterladen' })).toBeDisabled();

		// September da: der Name gehört zu Zeitraum und Auto der Sessions, die jetzt dastehen
		api.stats.mockResolvedValue(stats());
		navigate('?zeitraum=monat&datum=2026-09-16&auto=tesla');
		await vi.advanceTimersByTimeAsync(0);
		await fireEvent.click(screen.getByRole('button', { name: 'CSV herunterladen' }));
		expect(downloads.at(-1)).toBe('ladesessions-2026-09-01-2026-09-30-tesla.csv');
	} finally {
		click.mockRestore();
		Reflect.deleteProperty(URL, 'createObjectURL');
		Reflect.deleteProperty(URL, 'revokeObjectURL');
	}
});
