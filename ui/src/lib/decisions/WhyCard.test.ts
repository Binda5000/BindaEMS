import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { Decision } from '$lib/api/schemas';
import { latestDecisions } from '$lib/testing/fixtures';
import WhyCard from './WhyCard.svelte';

it('zeigt Name als Verweis und Text in derselben Zeile, rechts das Alter', () => {
	const decision = latestDecisions().decisions[0];
	if (!decision) throw new Error('Demo ohne Entscheidung');
	const nowMs = Date.parse(decision.ts) + 3 * 60_000;
	render(WhyCard, {
		decisions: [decision],
		error: null,
		names: { [decision.actuator]: 'Victron (ESS)' },
		nowMs
	});
	const item = screen.getByRole('listitem');
	const link = within(item).getByRole('link', { name: 'Victron (ESS)' });
	expect(link).toHaveAttribute(
		'href',
		`/entscheidungen?actuator=${encodeURIComponent(decision.actuator)}`
	);
	expect(item).toHaveTextContent(decision.text);
	expect(within(item).getByText('vor 3 min')).toBeInTheDocument();
});

it('sagt, wenn es nichts zu erklären gibt', () => {
	render(WhyCard, { decisions: [], error: null, names: {}, nowMs: 0 });
	expect(screen.getByText('Keine Entscheidungen in den letzten 24 h.')).toBeInTheDocument();
});

function withText(text: string): Decision {
	const decision = latestDecisions().decisions[0];
	if (!decision) throw new Error('Demo ohne Entscheidung');
	return { ...decision, actuator: 'victron_ess', text };
}

function renderItem(text: string): HTMLElement {
	const decision = withText(text);
	render(WhyCard, {
		decisions: [decision],
		error: null,
		names: { victron_ess: 'Victron (ESS)' },
		nowMs: Date.parse(decision.ts)
	});
	return screen.getByRole('listitem');
}

it('nennt den Namen nur einmal, wenn der Text mit ihm beginnt', () => {
	const item = renderItem('Victron (ESS) (Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run.');
	expect(within(item).getAllByText(/Victron \(ESS\)/)).toHaveLength(1);
	expect(within(item).getByRole('link', { name: 'Victron (ESS)' })).toBeInTheDocument();
	expect(item).not.toHaveTextContent('·');
	expect(item.querySelector('.line')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		'Victron (ESS) (Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run.'
	);
});

it('setzt den Doppelpunkt ohne Leerzeichen an den Namen', () => {
	const item = renderItem('Victron (ESS): Netz-Sollwert 0 W – gesendet.');
	expect(item.querySelector('.line')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		'Victron (ESS): Netz-Sollwert 0 W – gesendet.'
	);
});

it('behält „Name · Text“, wenn der Text anders beginnt', () => {
	const item = renderItem('EVCS alt: Laden aus.');
	expect(within(item).getByRole('link', { name: 'Victron (ESS)' })).toBeInTheDocument();
	expect(item).toHaveTextContent('Victron (ESS) · EVCS alt: Laden aus.');
});
