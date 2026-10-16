import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { planBlocks } from './model';
import PlanBlocks from './PlanBlocks.svelte';
import { slot } from '$lib/testing/plan';

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
