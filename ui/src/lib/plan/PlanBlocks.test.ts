import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { planBlocks } from './model';
import PlanBlocks from './PlanBlocks.svelte';
import { PLAN, iso, slot } from '$lib/testing/plan';

it('shows time, intent, SOC from → to and the sentence of each block', () => {
	const blocks = planBlocks([
		slot('10:00', 'HALTEN', 0, 'HALTEN_FUER_SPITZE', {
			soc_pct: 40,
			text: 'Akku hält 40 %, weil 17:00–19:00 Uhr 35,0 ct erwartet werden.'
		}),
		slot('10:15', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG', {
			soc_pct: 47.4,
			text: 'Akku lädt 2,3 kW aus dem Netz: jetzt 12,0 ct, um 17:00 Uhr 35,0 ct.'
		})
	]);
	render(PlanBlocks, { blocks });
	expect(screen.getByRole('heading', { name: 'Ablauf' })).toBeTruthy();
	expect(screen.getByText('10:00–10:15')).toBeTruthy();
	expect(screen.getByText('Halten')).toBeTruthy();
	expect(screen.getByText('SOC 40 → 40 %')).toBeTruthy();
	expect(
		screen.getByText('Akku hält 40 %, weil 17:00–19:00 Uhr 35,0 ct erwartet werden.')
	).toBeTruthy();
	expect(screen.getByText('10:15–10:30')).toBeTruthy();
	expect(screen.getByText('Netzladen 2,3 kW')).toBeTruthy();
	expect(screen.getByText('SOC 40 → 47 %')).toBeTruthy();
	expect(
		screen.getByText('Akku lädt 2,3 kW aus dem Netz: jetzt 12,0 ct, um 17:00 Uhr 35,0 ct.')
	).toBeTruthy();
});

it('zeichnet erwartete Autos gestrichelt mit „erwartet ab“ im Block ihrer Ankunft', () => {
	const car = { ...PLAN.vehicles[0]!, expected: true, arrival_at: iso('10:15') };
	const blocks = planBlocks(
		[
			slot('10:00', 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH'),
			slot('10:15', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG')
		],
		[car]
	);
	render(PlanBlocks, { blocks });
	const label = screen.getByText('Tesla Model 3 erwartet ab 10:15');
	expect(label).toHaveClass('expected');
	expect(label.closest('li')).toHaveTextContent('10:15–10:30');
	expect(screen.getAllByText(/erwartet ab/)).toHaveLength(1);
});

it('zeigt den Satz zum Grundcode AUTO_ERWARTET wörtlich vom Server', () => {
	const text = 'Tesla Model 3 wird um 17:30 Uhr mit etwa 41 % erwartet (Anwesenheitsprognose).';
	const blocks = planBlocks([slot('17:30', 'EIGENVERBRAUCH', 0, 'AUTO_ERWARTET', { text })]);
	render(PlanBlocks, { blocks });
	expect(screen.getByText(text)).toBeVisible();
});

it('zeigt eine kleine SOC-Änderung mit einer Nachkommastelle', () => {
	const blocks = planBlocks(
		[slot('10:00', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG', { soc_pct: 19.9 })],
		[],
		19.2
	);
	render(PlanBlocks, { blocks });
	expect(screen.getByText('SOC 19,2 → 19,9 %')).toBeTruthy();
});
