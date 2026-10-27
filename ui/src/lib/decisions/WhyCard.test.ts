import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
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
