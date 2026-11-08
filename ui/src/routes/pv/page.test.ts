import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { page } from '$app/state';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { PvDay } from '$lib/api/schemas';
import { pvTree, pvWeek, pvYear } from '$lib/testing/pv';
import Page from './+page.svelte';

const { api, goto } = vi.hoisted(() => ({
	api: {
		pv: vi.fn(),
		pvPeriod: vi.fn(),
		pvDay: vi.fn(),
		pvCandidates: vi.fn(),
		createPv: vi.fn(),
		updatePv: vi.fn(),
		deletePv: vi.fn()
	},
	goto: vi.fn()
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));
vi.mock('$app/navigation', () => ({ goto }));
// reaktive URL wie nach einer Navigation; die Rolle setzen die Tests
vi.mock('$app/state', async () => {
	const { SvelteURL } = await import('svelte/reactivity');
	return {
		page: {
			url: new SvelteURL('https://ems.lan/pv'),
			data: { user: { username: 'gast', role: 'viewer' } }
		}
	};
});

function navigate(search: string) {
	page.url.search = search;
}

function role(value: 'viewer' | 'admin') {
	(page.data as { user: { role: string } }).user.role = value;
}

const DAY: PvDay = { date: '2026-11-08', series: [] };

beforeEach(() => {
	vi.useFakeTimers({
		toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout']
	});
	vi.setSystemTime(new Date('2026-11-08T11:00:00Z')); // 12:00 in Wien
	vi.resetAllMocks();
	navigate('');
	role('viewer');
	api.pv.mockResolvedValue(pvTree());
	api.pvPeriod.mockImplementation(async (period: string) =>
		period === 'year' ? pvYear() : pvWeek()
	);
	api.pvDay.mockResolvedValue(DAY);
	api.pvCandidates.mockResolvedValue({ core: ['pv.huawei.power_w'], ha: [], ha_error: null });
});

afterEach(() => {
	vi.useRealTimers();
});

it('lädt den Zeitraum aus der Adresse und blättert zum Vormonat', async () => {
	navigate('?zeitraum=monat&tag=2026-11-08');
	render(Page);
	expect(screen.getByRole('heading', { name: 'November 2026' })).toBeInTheDocument();
	await waitFor(() => expect(api.pvPeriod).toHaveBeenCalled());
	expect(api.pvPeriod.mock.lastCall?.slice(0, 2)).toEqual(['month', '2026-11-08']);
	expect(screen.getByRole('button', { name: 'Monat' })).toHaveAttribute('aria-pressed', 'true');

	await fireEvent.click(screen.getByRole('button', { name: 'Zurück' }));
	expect(goto).toHaveBeenLastCalledWith(
		'/pv?zeitraum=monat&tag=2026-10-08',
		expect.objectContaining({ keepFocus: true, noScroll: true })
	);
	expect(screen.getByRole('button', { name: 'Weiter' })).toBeDisabled();
	expect(screen.getByRole('button', { name: 'Heute' })).toBeDisabled();

	await fireEvent.click(screen.getByRole('button', { name: 'Jahr' }));
	expect(goto.mock.lastCall?.[0]).toBe('/pv?zeitraum=jahr&tag=2026-11-08');
});

it('zeigt am Tag die Kurve, im Monat die Balken', async () => {
	render(Page);
	expect(screen.getByRole('heading', { name: 'So., 08.11.2026' })).toBeInTheDocument();
	expect(await screen.findByRole('heading', { name: 'Leistung' })).toBeInTheDocument();
	expect(api.pvDay).toHaveBeenCalledWith('2026-11-08', expect.anything());
	expect(screen.queryByRole('heading', { name: 'Je Tag' })).not.toBeInTheDocument();

	navigate('?zeitraum=monat&tag=2026-11-08');
	expect(await screen.findByRole('heading', { name: 'Je Tag' })).toBeInTheDocument();
	expect(screen.queryByRole('heading', { name: 'Leistung' })).not.toBeInTheDocument();
});

it('zeigt PV gesamt und je Anlage eine Karte mit ihren Strings', async () => {
	navigate('?zeitraum=woche&tag=2026-11-08');
	render(Page);
	const line = await screen.findByTestId('pv-total');
	await waitFor(() => expect(line.textContent?.replace(/\s+/g, ' ')).toContain('49,6 kWh'));
	expect(line.textContent?.replace(/\s+/g, ' ')).toContain('jetzt 2,96 kW');
	expect(screen.queryByText(/davon nicht zugeordnet/)).not.toBeInTheDocument();
	expect(screen.getByRole('heading', { name: 'Huawei', level: 2 })).toBeInTheDocument();
	expect(screen.getByText('Diese Woche', { selector: 'dt' })).toBeInTheDocument();
	expect(screen.getByText('Süd')).toBeInTheDocument();
	expect(screen.queryByRole('button', { name: /hinzufügen/ })).not.toBeInTheDocument();
	expect(screen.queryByRole('button', { name: 'Huawei ändern' })).not.toBeInTheDocument();
});

it('nennt „davon nicht zugeordnet“, wenn es ins Gewicht fällt', async () => {
	const period = pvWeek();
	period.unassigned = { kwh: [], sum_kwh: 2.4 };
	api.pvPeriod.mockResolvedValue(period);
	render(Page);
	expect(await screen.findByText(/davon nicht zugeordnet 2,4/)).toBeInTheDocument();
});

it('aktualisiert Baum und laufenden Zeitraum alle 15 s, einen vergangenen nur einmal', async () => {
	navigate('?zeitraum=woche&tag=2026-11-08');
	render(Page);
	await vi.advanceTimersByTimeAsync(0);
	expect([api.pv.mock.calls.length, api.pvPeriod.mock.calls.length]).toEqual([1, 1]);
	await vi.advanceTimersByTimeAsync(15_000);
	expect([api.pv.mock.calls.length, api.pvPeriod.mock.calls.length]).toEqual([2, 2]);

	navigate('?zeitraum=woche&tag=2026-10-20');
	await vi.advanceTimersByTimeAsync(0);
	api.pvPeriod.mockClear();
	await vi.advanceTimersByTimeAsync(60_000);
	expect(api.pvPeriod).not.toHaveBeenCalled();
	expect(api.pv.mock.calls.length).toBeGreaterThanOrEqual(6);
});

it('lässt Admins eine Anlage anlegen', async () => {
	role('admin');
	api.pv.mockResolvedValue(pvTree([]));
	api.createPv.mockResolvedValue({});
	render(Page);
	expect(await screen.findByText(/Hier erscheint je PV-Anlage eine Karte/)).toBeInTheDocument();
	await fireEvent.click(screen.getByRole('button', { name: 'Anlage hinzufügen' }));
	const dialog = screen.getByRole('dialog', { name: 'Anlage hinzufügen' });
	await fireEvent.input(within(dialog).getByLabelText('Name'), { target: { value: 'Huawei' } });
	await fireEvent.input(within(dialog).getByLabelText('core-Signal'), {
		target: { value: 'pv.huawei.power_w' }
	});
	await fireEvent.input(within(dialog).getByLabelText(/^kWp/), { target: { value: '9,8' } });
	await fireEvent.click(within(dialog).getByRole('button', { name: 'Speichern' }));
	await waitFor(() => expect(api.createPv).toHaveBeenCalled());
	expect(api.createPv.mock.lastCall?.[0]).toMatchObject({
		name: 'Huawei',
		parent_id: null,
		source_kind: 'core',
		power_ref: 'pv.huawei.power_w',
		kwp: 9.8
	});
	expect(await screen.findByText('„Huawei“ angelegt.')).toBeInTheDocument();
	expect(api.pvCandidates).toHaveBeenCalledTimes(1);
});

it('lässt Admins eine Anlage mit Bestätigung löschen und meldet eine Ablehnung', async () => {
	role('admin');
	api.deletePv.mockRejectedValueOnce(
		new ApiError(409, 'Anlage hat Strings: zuerst die Strings löschen')
	);
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Huawei ändern' }));
	await fireEvent.click(screen.getByRole('button', { name: 'Löschen' }));
	expect(screen.getByText('„Huawei“ wirklich löschen?')).toBeInTheDocument();
	await fireEvent.click(screen.getByRole('button', { name: 'Endgültig löschen' }));
	expect(
		await screen.findByText('Anlage hat Strings: zuerst die Strings löschen')
	).toBeInTheDocument();
	expect(api.deletePv).toHaveBeenCalledWith(1);
});
