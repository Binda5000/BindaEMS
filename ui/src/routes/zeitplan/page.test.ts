import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as v from 'valibot';
import reviewJson from '$lib/api/contract/plan-review.json';
import { ApiError } from '$lib/api/errors';
import { PlanReviewSchema, type PlanResponse, type Role } from '$lib/api/schemas';
import { PLAN, RESPONSE, planWithWindows } from '$lib/testing/plan';
import Page from './+page.svelte';

const { api, session } = vi.hoisted(() => ({
	api: {
		plan: vi.fn(),
		planReview: vi.fn(),
		history: vi.fn(),
		replan: vi.fn(),
		setGridCharge: vi.fn()
	},
	session: { role: 'operator' as Role }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/state', () => ({
	page: {
		data: {
			get user() {
				return { id: 1, username: 'bedienung', role: session.role };
			}
		}
	}
}));
// ECharts wird nicht geladen: die Diagramme bleiben leer, die Karten darum herum sind das Thema
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

const REVIEW = v.parse(PlanReviewSchema, reviewJson);
const HISTORY = {
	rp: 'raw' as const,
	step_s: 900,
	series: {
		'soc.battery': { label: 'Akku-SOC', unit: '%', points: [[1, 50]] as [number, number][] }
	}
};

const WITH_WINDOWS: PlanResponse = { ...RESPONSE, plan: planWithWindows() };

function renderPage(role: Role, response: PlanResponse = WITH_WINDOWS): void {
	session.role = role;
	api.plan.mockResolvedValue(response);
	render(Page);
}

beforeEach(() => {
	vi.resetAllMocks();
	api.planReview.mockResolvedValue(REVIEW);
	api.history.mockResolvedValue(HISTORY);
	api.setGridCharge.mockResolvedValue({ grid_charge_allowed: true, settings_version: 5 });
	api.replan.mockResolvedValue(undefined);
});

const NEXT: PlanResponse = {
	...WITH_WINDOWS,
	grid_charge_allowed: true,
	plan: { ...planWithWindows(), id: 2, created_at: '2026-10-09T08:15:00+00:00' }
};

it('lets operators switch grid charging and replan', async () => {
	renderPage('operator');
	await fireEvent.click(await screen.findByRole('switch', { name: 'Netzladen erlaubt' }));
	await waitFor(() => expect(api.setGridCharge).toHaveBeenCalledWith(true));
	// der Schalter löst eine Neuplanung aus; die Seite wartet auf den neuen Plan, bevor sie erneut plant
	api.plan.mockResolvedValue(NEXT);
	await screen.findByText('Plan von 10:15 Uhr', undefined, { timeout: 5_000 });
	await waitFor(() => expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled());
	await fireEvent.click(screen.getByRole('button', { name: 'Neu planen' }));
	await waitFor(() => expect(api.replan).toHaveBeenCalled());
}, 15_000);

it('waits for the follow-up plan after the switch and shows "Plant …"', async () => {
	session.role = 'operator';
	// Ladung beim Öffnen, Ladung nach dem Schalter: noch der alte Plan, erst danach der neue
	api.plan
		.mockResolvedValueOnce(WITH_WINDOWS)
		.mockResolvedValueOnce(WITH_WINDOWS)
		.mockResolvedValue(NEXT);
	render(Page);
	await fireEvent.click(await screen.findByRole('switch', { name: 'Netzladen erlaubt' }));
	expect(await screen.findByRole('button', { name: 'Plant …' })).toBeDisabled();
	await screen.findByText('Plan von 10:15 Uhr', undefined, { timeout: 5_000 });
	await waitFor(() => expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled());
	expect(api.replan).not.toHaveBeenCalled();
	expect(screen.getByRole('switch', { name: 'Netzladen erlaubt' })).toBeChecked();
}, 15_000);

it('ignores "Neu planen" while the switch call is still running', async () => {
	let finish: (value: {
		grid_charge_allowed: boolean;
		settings_version: number;
	}) => void = () => {};
	api.setGridCharge.mockReturnValue(new Promise((resolve) => (finish = resolve)));
	renderPage('operator');
	await fireEvent.click(await screen.findByRole('switch', { name: 'Netzladen erlaubt' }));
	await waitFor(() => expect(api.setGridCharge).toHaveBeenCalledWith(true));
	// der Aufruf läuft noch: die Taste ist schon gesperrt, ein Klick plant nicht
	const button = screen.getByRole('button', { name: /^(Neu planen|Plant …)$/ });
	await fireEvent.click(button);
	expect(button).toBeDisabled();
	expect(api.replan).not.toHaveBeenCalled();
	api.plan.mockResolvedValue(NEXT);
	finish({ grid_charge_allowed: true, settings_version: 5 });
	await screen.findByText('Plan von 10:15 Uhr', undefined, { timeout: 5_000 });
	await waitFor(() => expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled());
	expect(api.replan).not.toHaveBeenCalled();
}, 15_000);

it('frees "Neu planen" again when the switch call fails', async () => {
	api.setGridCharge.mockRejectedValue(new ApiError(409, 'Einstellungen geändert'));
	renderPage('operator');
	await fireEvent.click(await screen.findByRole('switch', { name: 'Netzladen erlaubt' }));
	await screen.findByText('Einstellungen geändert');
	expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled();
});

it('shows the plan with blocks to viewers, without controls', async () => {
	renderPage('viewer');
	await screen.findByText('Plan von 10:00 Uhr');
	expect(screen.queryByRole('switch')).toBeNull();
	expect(screen.queryByRole('button', { name: 'Neu planen' })).toBeNull();
	const blocks = screen.getByRole('heading', { name: 'Ablauf' }).closest('section')!;
	expect(within(blocks).getByText('11:00–11:30')).toBeTruthy();
	expect(within(blocks).getByText('Halten')).toBeTruthy();
	expect(within(blocks).getByText('Netzladen 2,3 kW')).toBeTruthy();
	expect(screen.getByRole('img', { name: 'Preise und Prognosen' })).toBeTruthy();
	expect(screen.getByRole('img', { name: 'Akku' })).toBeTruthy();
});

it('shows the empty state', async () => {
	renderPage('viewer', {
		status: { ok: false, message: 'Kein Plan: keine PV-Prognose', running: false },
		grid_charge_allowed: false,
		plan: null,
		execution: null
	});
	expect(await screen.findByText('Noch kein Plan.')).toBeTruthy();
	expect(screen.getByText('Kein Plan: keine PV-Prognose')).toBeTruthy();
	expect(screen.queryByRole('heading', { name: 'Ablauf' })).toBeNull();
});

it('asks again after replanning until the plan changes', async () => {
	const next = { ...RESPONSE, plan: { ...PLAN, id: 2, created_at: '2026-10-09T08:15:00+00:00' } };
	session.role = 'operator';
	api.plan.mockResolvedValueOnce(RESPONSE).mockResolvedValue(next);
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Neu planen' }));
	expect(await screen.findByRole('button', { name: 'Plant …' })).toBeDisabled();
	// die Seite fragt alle 2 s, bis der Plan wechselt
	await screen.findByText('Plan von 10:15 Uhr', undefined, { timeout: 5_000 });
	expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled();
}, 15_000);

it('shows the error of a failed request and keeps the plan', async () => {
	renderPage('operator');
	api.replan.mockRejectedValue(new ApiError(503, 'Planung nicht erreichbar'));
	await fireEvent.click(await screen.findByRole('button', { name: 'Neu planen' }));
	expect(await screen.findByText('Planung nicht erreichbar')).toBeTruthy();
	expect(screen.getByText('Plan von 10:00 Uhr')).toBeTruthy();
	expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled();
});

it('shows the load error of the plan request', async () => {
	session.role = 'viewer';
	api.plan.mockRejectedValue(new ApiError(500, 'Fehler beim Laden'));
	render(Page);
	expect(await screen.findByText('Fehler beim Laden')).toBeTruthy();
});

it('shows the card "Plan gegen Ist (heute)" with the SOC of the day of the review', async () => {
	renderPage('viewer');
	const card = (await screen.findByRole('heading', { name: 'Plan gegen Ist (heute)' })).closest(
		'section'
	)!;
	await within(card).findByRole('table');
	expect(within(card).getByText('Netzbezug')).toBeTruthy();
	expect(within(card).getByText('Bezugskosten')).toBeTruthy();
	expect(within(card).getByRole('img', { name: 'Plan gegen Ist (heute)' })).toBeTruthy();
	// der Tag kommt vom Server, nicht von der Uhr des Browsers
	await waitFor(() =>
		expect(api.history).toHaveBeenLastCalledWith(
			['soc.battery'],
			{ from: '2026-10-08T22:00:00.000Z', to: '2026-10-09T22:00:00.000Z' },
			expect.anything()
		)
	);
});

it('keeps the plan and says so when "Plan gegen Ist" fails', async () => {
	api.planReview.mockRejectedValue(new ApiError(500, 'Interner Fehler'));
	renderPage('viewer');
	expect(await screen.findByText('Plan gegen Ist ist nicht verfügbar.')).toBeTruthy();
	expect(screen.getByText('Plan von 10:00 Uhr')).toBeTruthy();
	expect(screen.queryByText('Interner Fehler')).toBeNull();
});

it('zeigt erwartete Autos im Ablauf gestrichelt mit „erwartet ab“', async () => {
	const expected = {
		...PLAN.vehicles[0]!,
		key: 'egolf',
		name: 'e-Golf',
		expected: true,
		arrival_at: '2026-10-09T15:30:00+00:00'
	};
	const plan = { ...planWithWindows(), vehicles: [...PLAN.vehicles, expected] };
	renderPage('viewer', { ...RESPONSE, plan });
	await screen.findByText('Plan von 10:00 Uhr');
	const blocks = screen.getByRole('heading', { name: 'Ablauf' }).closest('section')!;
	expect(within(blocks).getByText('e-Golf erwartet ab 17:30')).toHaveClass('expected');
	// auch im Kopf des Plans steht das Auto als erwartet
	expect(screen.getAllByText('e-Golf erwartet ab 17:30')).toHaveLength(2);
});

const NO_PLAN = 'Kein neuer Plan nach 40 s. Die Seite zeigt den letzten.';

describe('Warten auf den neuen Plan', () => {
	beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
	afterEach(() => vi.useRealTimers());

	it('sagt nach 40 s ohne neuen Plan, dass die Seite den letzten zeigt', async () => {
		renderPage('operator'); // api.plan liefert immer denselben Plan
		await fireEvent.click(await screen.findByRole('switch', { name: 'Netzladen erlaubt' }));
		expect(await screen.findByRole('button', { name: 'Plant …' })).toBeDisabled();
		expect(screen.queryByText(NO_PLAN)).toBeNull();
		await vi.advanceTimersByTimeAsync(30_000);
		expect(screen.queryByText(NO_PLAN)).toBeNull();
		await vi.advanceTimersByTimeAsync(12_000);
		expect(await screen.findByText(NO_PLAN)).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled();
		expect(screen.getByText('Plan von 10:00 Uhr')).toBeTruthy();
	});

	it('nennt dasselbe nach „Neu planen“ und löscht den Hinweis beim nächsten Versuch', async () => {
		renderPage('operator');
		await fireEvent.click(await screen.findByRole('button', { name: 'Neu planen' }));
		await vi.advanceTimersByTimeAsync(42_000);
		expect(await screen.findByText(NO_PLAN)).toBeTruthy();
		await fireEvent.click(screen.getByRole('button', { name: 'Neu planen' }));
		await waitFor(() => expect(screen.queryByText(NO_PLAN)).toBeNull());
	});

	it('wartet nicht, wenn der Schalter die Version der Einstellungen nicht ändert', async () => {
		renderPage('operator');
		await fireEvent.click(await screen.findByRole('switch', { name: 'Netzladen erlaubt' }));
		api.plan.mockResolvedValue(NEXT);
		await screen.findByText('Plan von 10:15 Uhr', undefined, { timeout: 5_000 });
		await waitFor(() => expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled());
		// dieselbe Version wie bei der Antwort davor: nichts geändert, keine Neuplanung
		await fireEvent.click(screen.getByRole('switch', { name: 'Netzladen erlaubt' }));
		await waitFor(() => expect(api.setGridCharge).toHaveBeenCalledTimes(2));
		await waitFor(() =>
			expect(screen.getByRole('switch', { name: 'Netzladen erlaubt' })).toBeEnabled()
		);
		expect(screen.queryByRole('button', { name: 'Plant …' })).toBeNull();
		expect(screen.getByRole('button', { name: 'Neu planen' })).toBeEnabled();
	});
});

describe('Vorheizen und Vorkühlen', () => {
	/** Die Karte „Vorheizen und Vorkühlen“ */
	function thermalCard(): HTMLElement {
		const section = screen
			.getByRole('heading', { name: 'Vorheizen und Vorkühlen' })
			.closest('section');
		if (!section) throw new Error('Karte Vorheizen und Vorkühlen fehlt');
		return section;
	}

	function withThermal(changes: Partial<NonNullable<typeof PLAN.thermal>>): PlanResponse {
		return { ...RESPONSE, plan: { ...PLAN, thermal: { ...PLAN.thermal!, ...changes } } };
	}

	it('zeigt im Zeitplan die Spalte Klima und die Vorschau', async () => {
		renderPage('viewer', withThermal({ saving_ct: 12 }));
		await screen.findByRole('heading', { name: 'Vorheizen und Vorkühlen' });
		const card = within(thermalCard());
		expect(card.getByText('Vorschau: Vorheizen/Vorkühlen würde etwa 12 ct sparen.')).toBeVisible();
		const table = card.getByRole('table');
		expect(
			within(table)
				.getAllByRole('columnheader')
				.map((header) => header.textContent)
		).toEqual(['Zeit', 'Klima']);
		const rows = within(table)
			.getAllByRole('row')
			.slice(1)
			.map((row) =>
				[...row.querySelectorAll('th, td')].map((cell) =>
					cell.textContent?.replace(/\s+/g, ' ').trim()
				)
			);
		// Demo-Vorschau beim Heizen: +1, +2, +1 Stufen am Nachmittag, −1 am Abend
		expect(rows).toEqual([
			['10:00–13:30', '–'],
			['13:30–16:00', '+0,5 K'],
			['16:00–17:00', '+1,0 K'],
			['17:00–18:00', '+0,5 K'],
			['18:00–19:00', '–'],
			['19:00–20:00', '−0,5 K'],
			['20:00–00:00', '–']
		]);
	});

	it('zeigt die aktive Verschiebung mit der Ersparnis', async () => {
		renderPage('viewer', withThermal({ active: true, saving_ct: 18.4 }));
		await screen.findByRole('heading', { name: 'Vorheizen und Vorkühlen' });
		expect(
			within(thermalCard()).getByText('Vorheizen/Vorkühlen aktiv: spart etwa 18,4 ct.')
		).toBeVisible();
	});

	it('nennt ohne Verschiebung den Grund und zeigt keine Tabelle', async () => {
		const response = withThermal({ reason: 'off', saving_ct: 0 });
		const plan = response.plan!;
		const slots = plan.slots.map((s) => ({ ...s, thermal_steps: null, thermal_w: 0 }));
		renderPage('viewer', { ...response, plan: { ...plan, slots } });
		await screen.findByRole('heading', { name: 'Vorheizen und Vorkühlen' });
		expect(within(thermalCard()).getByText('Vorheizen und Vorkühlen ist aus.')).toBeVisible();
		expect(within(thermalCard()).queryByRole('table')).toBeNull();
	});

	it('fehlt ohne Klimaanlage', async () => {
		renderPage('viewer', { ...RESPONSE, plan: { ...PLAN, thermal: null } });
		await screen.findByRole('heading', { name: 'Ablauf' });
		expect(screen.queryByRole('heading', { name: 'Vorheizen und Vorkühlen' })).toBeNull();
	});
});
