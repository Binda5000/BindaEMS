import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import { decisions, system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, goto, location } = vi.hoisted(() => ({
	api: { system: vi.fn(), decisions: vi.fn() },
	goto: vi.fn(),
	location: { url: 'https://ems.lan/entscheidungen' }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$app/state', () => ({
	page: {
		get url() {
			return new URL(location.url);
		}
	}
}));

beforeEach(() => {
	vi.resetAllMocks();
	location.url = 'https://ems.lan/entscheidungen';
	api.system.mockResolvedValue(system());
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
	location.url =
		'https://ems.lan/entscheidungen?range=week&actuator=wallbox.evcs&result=dry_run&q=%C3%9Cberschuss';
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

it('meldet einen Fehler des Servers', async () => {
	api.decisions.mockRejectedValue(
		new (await import('$lib/api/errors')).ApiError(503, 'core nicht erreichbar.')
	);
	render(Page);
	expect(await screen.findByText('core nicht erreichbar.')).toBeInTheDocument();
	expect(screen.queryByText('Keine Entscheidungen im Zeitraum.')).not.toBeInTheDocument();
});
