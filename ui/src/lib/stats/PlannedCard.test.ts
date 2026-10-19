import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { Stats } from '$lib/api/schemas';
import { stats } from '$lib/testing/fixtures';
import PlannedCard from './PlannedCard.svelte';

const NONE: Stats['planned'] = {
	reached: 0,
	feasible: 0,
	missed: 0,
	infeasible: 0,
	early: 0,
	unknown: 0,
	changed: 0
};

function show(planned: Partial<Stats['planned']>) {
	render(PlannedCard, { stats: stats({ planned: { ...NONE, ...planned } }) });
}

it('zeigt die Quote der machbaren Ladungen und daneben, was nicht zählt', () => {
	show({ reached: 19, feasible: 20, missed: 1, infeasible: 2, early: 3, unknown: 1, changed: 4 });
	expect(screen.getByRole('heading', { name: 'Geplantes Laden' })).toBeVisible();
	// testing-library vergleicht mit Leerzeichen statt geschützter Leerzeichen
	expect(screen.getByText('19 von 20 machbaren Ladungen erreicht (95 %)')).toBeVisible();
	expect(
		screen.getByText('Nicht machbar: 2 · Vor der Abfahrt abgesteckt: 3 · Ohne Ladestand: 1')
	).toBeVisible();
	expect(screen.getByText('Vor der Abfahrt geändert oder verlassen: 4')).toBeVisible();
});

it('zeigt die Demo-Zählung aus dem Vertrag (Oktober)', () => {
	render(PlannedCard, { stats: stats() });
	expect(screen.getByText('1 von 1 machbaren Ladungen erreicht (100 %)')).toBeVisible();
	expect(
		screen.getByText('Nicht machbar: 0 · Vor der Abfahrt abgesteckt: 0 · Ohne Ladestand: 0')
	).toBeVisible();
	expect(screen.getByText('Vor der Abfahrt geändert oder verlassen: 1')).toBeVisible();
});

it('rundet die Quote nicht auf 100 %, solange eine Ladung fehlt', () => {
	show({ reached: 199, feasible: 200, missed: 1 });
	expect(screen.getByText('199 von 200 machbaren Ladungen erreicht (99 %)')).toBeVisible();
});

it('nennt ohne machbare Ladung keine Quote', () => {
	show({ infeasible: 1 });
	expect(screen.getByText('0 von 0 machbaren Ladungen erreicht (– %)')).toBeVisible();
});

it('sagt, wenn es im Zeitraum keine geplante Ladung gab', () => {
	show({});
	expect(screen.getByText('Im Zeitraum gab es keine geplante Ladung.')).toBeVisible();
	expect(screen.queryByText(/machbaren Ladungen/)).toBeNull();
});
