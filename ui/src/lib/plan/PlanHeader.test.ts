import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import PlanHeader from './PlanHeader.svelte';
import { PLAN, RESPONSE } from '$lib/testing/plan';

it('shows source, validity and the fallback reason', () => {
	render(PlanHeader, {
		data: {
			...RESPONSE,
			plan: { ...RESPONSE.plan!, source: 'fallback', fallback_reason: 'Zeitlimit ohne Lösung' }
		},
		canOperate: false
	});
	expect(screen.getByText('Ersatzplan')).toBeTruthy();
	expect(screen.getByText('Grund: Zeitlimit ohne Lösung')).toBeTruthy();
	expect(screen.queryByRole('button', { name: 'Neu planen' })).toBeNull();
});

it('shows start, validity, trigger and the costs against Victron alone', () => {
	// die Demo hat einen Leistungspreis (mit Budget); dieser Test will den Plan ohne
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, power_price: false } },
		canOperate: false
	});
	expect(screen.getByText('Plan von 10:00 Uhr')).toBeTruthy();
	expect(screen.getByText('Optimierer')).toBeTruthy();
	expect(screen.getByText('gültig bis 10:45 Uhr')).toBeTruthy();
	expect(screen.getByText('Anlass: Start')).toBeTruthy();
	expect(screen.getByText('Erwartete Kosten')).toBeTruthy();
	expect(screen.getByText('Victron allein')).toBeTruthy();
	expect(screen.queryByText('Budget')).toBeNull();
	expect(screen.queryByText(/^Grund:/)).toBeNull();
});

it('says when the plan has expired', () => {
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, valid: false } },
		canOperate: false
	});
	expect(screen.getByText('Plan abgelaufen')).toBeTruthy();
	expect(screen.queryByText(/^gültig bis/)).toBeNull();
});

it('shows the budget only with a power price', () => {
	const slots = PLAN.slots.map((s) => ({ ...s, budget_kw: 4.2 }));
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, power_price: true, slots } },
		canOperate: false
	});
	expect(screen.getByText('Budget')).toBeTruthy();
	expect(screen.getByText('4,2 kW')).toBeTruthy();
});

it('lists the issues', () => {
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, issues: ['Preis geschätzt ab 22:00'] } },
		canOperate: false
	});
	expect(screen.getByText('Auffälligkeiten')).toBeTruthy();
	expect(screen.getByText('Preis geschätzt ab 22:00')).toBeTruthy();
});

it('only shows the grid-charge setting to viewers', () => {
	render(PlanHeader, {
		data: { ...RESPONSE, grid_charge_allowed: true },
		canOperate: false
	});
	expect(screen.queryByRole('switch')).toBeNull();
	expect(screen.getByText('Netzladen erlaubt')).toBeTruthy();
	expect(screen.getByText('ja')).toBeTruthy();
});

it('shows "Plant …" while a run is going', () => {
	render(PlanHeader, { data: RESPONSE, canOperate: true, replanning: true });
	expect(screen.getByRole('button', { name: 'Plant …' })).toBeDisabled();
});

it('shows the empty state with the status', () => {
	render(PlanHeader, {
		data: {
			status: { ok: false, message: 'Kein Plan: keine PV-Prognose', running: false },
			grid_charge_allowed: false,
			plan: null,
			execution: null
		},
		canOperate: false
	});
	expect(screen.getByText('Noch kein Plan.')).toBeTruthy();
	expect(screen.getByText('Kein Plan: keine PV-Prognose')).toBeTruthy();
});

it('trusts the server for validity, not the clock of the browser', () => {
	// valid_until liegt lange zurück, der Server sagt „gültig“: die Anzeige folgt dem Server
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, valid: true, valid_until: '2020-07-01T00:00:00+00:00' } },
		canOperate: false
	});
	expect(screen.getByText('gültig bis 02:00 Uhr')).toBeTruthy();
	expect(screen.queryByText('Plan abgelaufen')).toBeNull();
});

const EXECUTION_TEXT = {
	pending: 'Der Plan ist noch nicht beim core.',
	dry_run: 'Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron.',
	live: 'Der core führt den Plan aus.'
} as const;

it('shows the text of the execution state instead of a preview line', () => {
	render(PlanHeader, {
		data: { ...RESPONSE, execution: { state: 'dry_run', text: EXECUTION_TEXT.dry_run } },
		canOperate: true
	});
	expect(screen.getByText(EXECUTION_TEXT.dry_run)).toBeTruthy();
	expect(screen.queryByText(/Vorschau/)).toBeNull();
});

it('marks every state but live as a warning', () => {
	const { unmount } = render(PlanHeader, {
		data: { ...RESPONSE, execution: { state: 'pending', text: EXECUTION_TEXT.pending } },
		canOperate: false
	});
	expect(screen.getByText(EXECUTION_TEXT.pending).classList.contains('live')).toBe(false);
	expect(screen.getByText(EXECUTION_TEXT.pending).classList.contains('execution')).toBe(true);
	unmount();
	render(PlanHeader, {
		data: { ...RESPONSE, execution: { state: 'live', text: EXECUTION_TEXT.live } },
		canOperate: false
	});
	const live = screen.getByText(EXECUTION_TEXT.live);
	expect(live.classList.contains('execution') && live.classList.contains('live')).toBe(true);
});

it('shows no execution line without a valid plan', () => {
	const { container } = render(PlanHeader, {
		data: { ...RESPONSE, plan: null, execution: null },
		canOperate: false
	});
	expect(container.querySelector('.execution')).toBeNull();
	expect(screen.getByText('Noch kein Plan.')).toBeTruthy();
});
