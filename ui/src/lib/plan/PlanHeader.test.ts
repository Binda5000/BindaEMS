import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import PlanHeader from './PlanHeader.svelte';
import { NOW, PLAN, RESPONSE } from '$lib/testing/plan';

it('shows source, validity and the fallback reason', () => {
	render(PlanHeader, {
		data: {
			...RESPONSE,
			plan: { ...RESPONSE.plan!, source: 'fallback', fallback_reason: 'Zeitlimit ohne Lösung' }
		},
		canOperate: false,
		now: NOW
	});
	expect(screen.getByText('Ersatzplan')).toBeTruthy();
	expect(screen.getByText('Grund: Zeitlimit ohne Lösung')).toBeTruthy();
	expect(screen.queryByRole('button', { name: 'Neu planen' })).toBeNull();
});

it('shows start, validity, trigger and the costs against Victron alone', () => {
	render(PlanHeader, { data: RESPONSE, canOperate: false, now: NOW });
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
		canOperate: false,
		now: NOW
	});
	expect(screen.getByText('Plan abgelaufen')).toBeTruthy();
	expect(screen.queryByText(/^gültig bis/)).toBeNull();
});

it('shows the budget only with a power price', () => {
	const slots = PLAN.slots.map((s) => ({ ...s, budget_kw: 4.2 }));
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, power_price: true, slots } },
		canOperate: false,
		now: NOW
	});
	expect(screen.getByText('Budget')).toBeTruthy();
	expect(screen.getByText('4,2 kW')).toBeTruthy();
});

it('lists the issues', () => {
	render(PlanHeader, {
		data: { ...RESPONSE, plan: { ...PLAN, issues: ['Preis geschätzt ab 22:00'] } },
		canOperate: false,
		now: NOW
	});
	expect(screen.getByText('Auffälligkeiten')).toBeTruthy();
	expect(screen.getByText('Preis geschätzt ab 22:00')).toBeTruthy();
});

it('only shows the grid-charge setting to viewers', () => {
	render(PlanHeader, {
		data: { ...RESPONSE, grid_charge_allowed: true },
		canOperate: false,
		now: NOW
	});
	expect(screen.queryByRole('switch')).toBeNull();
	expect(screen.getByText('Netzladen erlaubt')).toBeTruthy();
	expect(screen.getByText('ja')).toBeTruthy();
});

it('shows "Plant …" while a run is going', () => {
	render(PlanHeader, { data: RESPONSE, canOperate: true, now: NOW, replanning: true });
	expect(screen.getByRole('button', { name: 'Plant …' })).toBeDisabled();
});

it('shows the empty state with the status', () => {
	render(PlanHeader, {
		data: {
			status: { ok: false, message: 'Kein Plan: keine PV-Prognose', running: false },
			grid_charge_allowed: false,
			plan: null
		},
		canOperate: false,
		now: NOW
	});
	expect(screen.getByText('Noch kein Plan.')).toBeTruthy();
	expect(screen.getByText('Kein Plan: keine PV-Prognose')).toBeTruthy();
});

it('counts a plan as expired once its end has passed, even if the answer is older', () => {
	render(PlanHeader, { data: RESPONSE, canOperate: false, now: '2026-10-09T08:46:00+00:00' });
	expect(screen.getByText('Plan abgelaufen')).toBeTruthy();
});
