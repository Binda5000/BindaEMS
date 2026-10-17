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
	render(PlanHeader, { data: RESPONSE, canOperate: false });
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
			plan: null
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

it('says that the core does not run the plan yet, with and without a plan', () => {
	const preview = 'Vorschau – der core führt den Plan noch nicht aus.';
	const { unmount } = render(PlanHeader, { data: RESPONSE, canOperate: true });
	expect(screen.getByText(preview)).toBeTruthy();
	unmount();
	render(PlanHeader, { data: { ...RESPONSE, plan: null }, canOperate: false });
	expect(screen.getByText(preview)).toBeTruthy();
});
