import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { page } from '$app/state';
import { beforeEach, expect, it, vi } from 'vitest';
import { decisions, limits, system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, goto } = vi.hoisted(() => ({
	api: { system: vi.fn(), decisions: vi.fn(), limits: vi.fn() },
	goto: vi.fn()
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/navigation', () => ({ goto }));
// reaktive URL: Tests ändern sie wie die Navigation des Browsers
vi.mock('$app/state', async () => {
	const { SvelteURL } = await import('svelte/reactivity');
	return { page: { url: new SvelteURL('https://ems.lan/entscheidungen') } };
});

/** Die URL der Seite wie nach einer Navigation setzen */
function navigate(search: string) {
	page.url.search = search;
}

beforeEach(() => {
	vi.resetAllMocks();
	navigate('');
	api.system.mockResolvedValue(system());
	api.limits.mockReturnValue(new Promise(() => {}));
});

/** Letzte Abfrage von `api.decisions` */
function lastQuery() {
	return api.decisions.mock.lastCall?.[0];
}

it('filtert nach Aktor ohne Dry-Run', async () => {
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findByText(
		'Victron (ESS) (Überschuss): Netz-Sollwert 50 W – Dry-Run, nicht gesendet.'
	);
	expect(lastQuery()).toEqual({ since: expect.any(String), limit: 100 });

	// die Aktoren kommen aus Health; erst danach ist „Tesla Model 3“ wählbar
	await screen.findByRole('option', { name: 'Tesla Model 3' });
	await fireEvent.change(screen.getByLabelText('Aktor'), { target: { value: 'vehicle.tesla' } });
	await fireEvent.click(screen.getByLabelText('Dry-Run zeigen'));
	await waitFor(() =>
		expect(lastQuery()).toMatchObject({ actuator: 'vehicle.tesla', dry_run: false })
	);
	// der Filter steht danach in der URL
	await waitFor(() => {
		expect(goto.mock.lastCall?.[0]).toBe('/entscheidungen?actuator=vehicle.tesla&dry_run=false');
		expect(goto.mock.lastCall?.[1]).toMatchObject({ replaceState: true });
	});
});

it('öffnet gefiltert, wenn die URL Filter trägt', async () => {
	navigate('?range=week&actuator=wallbox.evcs&result=dry_run&q=%C3%9Cberschuss');
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findAllByRole('row');
	expect(lastQuery()).toMatchObject({
		actuator: 'wallbox.evcs',
		result: 'dry_run',
		text: 'Überschuss',
		limit: 100
	});
	expect(screen.getByLabelText('Zeitraum')).toHaveValue('week');
	expect(screen.getByLabelText('Ergebnis')).toHaveValue('dry_run');
	expect(screen.getByLabelText('Suche im Text')).toHaveValue('Überschuss');
	expect(goto).not.toHaveBeenCalled(); // die URL stimmt schon
});

it('sucht im Text, wenn das Feld verlassen wird', async () => {
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findAllByRole('row');
	const search = screen.getByLabelText('Suche im Text');
	await fireEvent.input(search, { target: { value: ' Strom ' } });
	await fireEvent.change(search);
	await waitFor(() => expect(lastQuery()).toMatchObject({ text: 'Strom' }));
});

it('lädt ältere mit dem Cursor', async () => {
	const first = decisions();
	const older = {
		decisions: [
			{ ...first.decisions[0]!, id: 'demo-older', cursor: 7, text: 'Ältere Entscheidung.' }
		],
		more: false
	};
	api.decisions.mockResolvedValueOnce({ ...first, more: true }).mockResolvedValueOnce(older);
	render(Page);
	const button = await screen.findByRole('button', { name: 'Ältere laden' });
	const cursor = first.decisions.at(-1)?.cursor;
	await fireEvent.click(button);
	expect(await screen.findByText('Ältere Entscheidung.')).toBeInTheDocument();
	expect(api.decisions).toHaveBeenLastCalledWith(
		expect.objectContaining({ before: cursor, limit: 100 }),
		expect.anything()
	);
	// die ersten Zeilen bleiben, die neue hängt an
	const rows = screen.getAllByRole('row');
	expect(rows).toHaveLength(1 + first.decisions.length + 1);
	expect(within(rows.at(-1)!).getByText('Ältere Entscheidung.')).toBeInTheDocument();
	expect(screen.queryByRole('button', { name: 'Ältere laden' })).not.toBeInTheDocument();
});

it('zeigt den leeren Zeitraum', async () => {
	api.decisions.mockResolvedValue({ decisions: [], more: false });
	render(Page);
	expect(await screen.findByText('Keine Entscheidungen im Zeitraum.')).toBeInTheDocument();
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('zeigt Spalten, Aktornamen und Ergebnisse', async () => {
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findAllByRole('row');
	expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
		'Zeit',
		'Aktor',
		'Entscheidung',
		'Ergebnis'
	]);
	const tesla = screen.getByText(/Min\+PV, Überschuss/).closest('tr')!;
	expect(within(tesla).getByText('Tesla Model 3', { selector: 'td' })).toBeInTheDocument();
	expect(within(tesla).getByText('zurückgestellt')).toBeInTheDocument();
});

it('nennt die Wallbox in Auswahl, Spalte und Text beim Namen aus den Einstellungen', async () => {
	api.limits.mockResolvedValue(limits());
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	expect(await screen.findByRole('option', { name: 'EVCS' })).toBeInTheDocument();
	const row = (await screen.findAllByText(/^EVCS \(PV-Überschuss, Überschuss\)/))[0]!.closest(
		'tr'
	)!;
	expect(within(row).getByText('EVCS', { selector: 'td' })).toBeInTheDocument();
	expect(screen.queryByText(/„evcs“/)).not.toBeInTheDocument();
});

it('meldet einen Fehler des Servers', async () => {
	api.decisions.mockRejectedValue(
		new (await import('$lib/api/errors')).ApiError(503, 'core nicht erreichbar.')
	);
	render(Page);
	expect(await screen.findByText('core nicht erreichbar.')).toBeInTheDocument();
	expect(screen.queryByText('Keine Entscheidungen im Zeitraum.')).not.toBeInTheDocument();
});

/** Eine Antwort, die der Test selbst abschließt */
function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => (resolve = done));
	return { promise, resolve };
}

it('sperrt „Ältere laden“ und dimmt die Zeilen, solange ein Filter neu lädt', async () => {
	const first = decisions();
	const reload = deferred<typeof first>();
	api.decisions.mockResolvedValueOnce({ ...first, more: true }).mockReturnValueOnce(reload.promise);
	render(Page);
	const button = await screen.findByRole('button', { name: 'Ältere laden' });
	expect(button).toBeEnabled();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();

	await fireEvent.click(screen.getByLabelText('Dry-Run zeigen'));
	// die alten Zeilen bleiben stehen, mit Hinweis; das Nachladen ist gesperrt
	expect(await screen.findByText('Lädt …')).toBeInTheDocument();
	expect(screen.getAllByRole('row')).toHaveLength(1 + first.decisions.length);
	expect(screen.getByRole('button', { name: 'Ältere laden' })).toBeDisabled();

	reload.resolve({ decisions: [], more: false });
	expect(await screen.findByText('Keine Entscheidungen im Zeitraum.')).toBeInTheDocument();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();
});

it('hängt eine späte Seite mit altem Cursor nicht an die neue Abfrage', async () => {
	const first = decisions();
	const older = deferred<typeof first>();
	const filtered = {
		decisions: [{ ...first.decisions[1]!, id: 'neu', text: 'Gefilterte Zeile.' }],
		more: false
	};
	api.decisions
		.mockResolvedValueOnce({ ...first, more: true })
		.mockReturnValueOnce(older.promise)
		.mockResolvedValueOnce(filtered);
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Ältere laden' }));
	await waitFor(() => expect(api.decisions).toHaveBeenCalledTimes(2));

	await fireEvent.click(screen.getByLabelText('Dry-Run zeigen')); // neuer Filter, solange „Ältere“ lädt
	expect(await screen.findByText('Gefilterte Zeile.')).toBeInTheDocument();
	older.resolve({
		decisions: [{ ...first.decisions[0]!, id: 'alt', cursor: 99, text: 'Alte Seite.' }],
		more: true
	});
	await new Promise((done) => setTimeout(done, 0));
	expect(screen.queryByText('Alte Seite.')).not.toBeInTheDocument();
	expect(screen.getAllByRole('row')).toHaveLength(2);
	expect(screen.queryByRole('button', { name: 'Ältere laden' })).not.toBeInTheDocument(); // more: false
});

it('setzt die Filter zurück, wenn die URL von außen wechselt', async () => {
	navigate('?actuator=wallbox.evcs&result=dry_run');
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findAllByRole('row');
	expect(screen.getByLabelText('Ergebnis')).toHaveValue('dry_run');

	// Klick auf „Entscheidungen“ in der Navigation: gleiche Seite, ohne Filter
	navigate('');
	await waitFor(() => expect(screen.getByLabelText('Ergebnis')).toHaveValue(''));
	expect(lastQuery()).toEqual({ since: expect.any(String), limit: 100 });
	expect(goto).not.toHaveBeenCalled(); // die alten Filter werden nicht zurückgeschrieben

	// Zurück/Vor zu einer Adresse mit Filtern
	navigate('?actuator=vehicle.tesla&dry_run=false&q=Strom');
	await waitFor(() =>
		expect(lastQuery()).toMatchObject({ actuator: 'vehicle.tesla', dry_run: false, text: 'Strom' })
	);
	expect(screen.getByLabelText('Dry-Run zeigen')).not.toBeChecked();
	expect(screen.getByLabelText('Suche im Text')).toHaveValue('Strom');
	expect(goto).not.toHaveBeenCalled();
});

it('lädt nicht erneut, wenn die URL nur die eigene Änderung wiedergibt', async () => {
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findAllByRole('row');
	await fireEvent.click(screen.getByLabelText('Dry-Run zeigen'));
	await waitFor(() => expect(goto).toHaveBeenCalledTimes(1));
	const calls = api.decisions.mock.calls.length;

	navigate('?dry_run=false'); // die Antwort von `goto`
	await new Promise((done) => setTimeout(done, 0));
	expect(api.decisions).toHaveBeenCalledTimes(calls);
	expect(goto).toHaveBeenCalledTimes(1);
	expect(screen.getByLabelText('Dry-Run zeigen')).not.toBeChecked();
});

it('behält eine neuere Eingabe, wenn die URL der vorigen erst danach ankommt', async () => {
	api.decisions.mockResolvedValue(decisions());
	render(Page);
	await screen.findAllByRole('row');
	await fireEvent.click(screen.getByLabelText('Dry-Run zeigen'));
	await fireEvent.change(screen.getByLabelText('Ergebnis'), { target: { value: 'ok' } });
	await waitFor(() => expect(goto).toHaveBeenCalledTimes(2));

	navigate('?dry_run=false'); // die URL der ersten Änderung kommt verspätet
	await new Promise((done) => setTimeout(done, 0));
	expect(screen.getByLabelText('Ergebnis')).toHaveValue('ok');
	navigate('?result=ok&dry_run=false');
	await new Promise((done) => setTimeout(done, 0));
	expect(screen.getByLabelText('Ergebnis')).toHaveValue('ok');
	expect(goto).toHaveBeenCalledTimes(2);
});
