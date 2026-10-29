import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { Decision } from '$lib/api/schemas';
import { latestDecisions } from '$lib/testing/fixtures';
import WhyCard from './WhyCard.svelte';

it('macht die ganze Zeile zum Verweis mit Name, Text, Alter und Etikett', () => {
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
	const link = within(item).getByRole('link');
	expect(link).toHaveAttribute(
		'href',
		`/entscheidungen?actuator=${encodeURIComponent(decision.actuator)}`
	);
	expect(within(link).getByText('Victron (ESS)')).toBeInTheDocument();
	expect(within(link).getByText('Überschuss: Netz-Sollwert 50 W')).toBeInTheDocument();
	expect(within(link).getByText('vor 3 min')).toBeInTheDocument();
	expect(within(link).getByText('Dry-Run')).toBeInTheDocument();
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

it('nennt den Namen nur einmal und zeigt Gründe und Befehl ohne Ergebnis', () => {
	const item = renderItem('Victron (ESS) (Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run.');
	expect(within(item).getAllByText(/Victron \(ESS\)/)).toHaveLength(1);
	expect(within(item).getByText('Plan: Netzladen: Netz-Sollwert 6070 W')).toBeInTheDocument();
	expect(within(item).getByText('Dry-Run')).toBeInTheDocument();
});

it('zeigt ohne Gründe nur den Befehl', () => {
	const item = renderItem('Victron (ESS): Netz-Sollwert 0 W – gesendet.');
	expect(within(item).getByText('Netz-Sollwert 0 W')).toBeInTheDocument();
});

it('zeigt den ganzen Text, wenn er anders beginnt als der Name', () => {
	const item = renderItem('EVCS alt: Laden aus.');
	expect(within(item).getByText('Victron (ESS)')).toBeInTheDocument();
	expect(within(item).getByText('EVCS alt: Laden aus.')).toBeInTheDocument();
});

it('tönt das Etikett bei zurückgestellten und abgelehnten Entscheidungen mit der Warnfarbe', () => {
	const decision = {
		...withText('Tesla: Strom 7 A – zurückgestellt.'),
		result: 'deferred' as const
	};
	render(WhyCard, { decisions: [decision], error: null, names: {}, nowMs: 0 });
	expect(screen.getByText('zurückgestellt')).toHaveClass('warn');
});

it('lässt das Etikett bei Dry-Run gedämpft', () => {
	const item = renderItem('Victron (ESS): Netz-Sollwert 0 W – Dry-Run, nicht gesendet.');
	expect(within(item).getByText('Dry-Run')).not.toHaveClass('warn');
});

it('zeigt die Wallbox mit dem Namen aus den Einstellungen und streicht die Bezeichnung des core', () => {
	const decision = {
		...withText('EVCS „evcs“ (PV-Überschuss): Strom 8 A – Dry-Run, nicht gesendet.'),
		actuator: 'wallbox.evcs'
	};
	render(WhyCard, {
		decisions: [decision],
		error: null,
		names: { 'wallbox.evcs': 'EVCS „evcs“' },
		wallboxNames: { evcs: 'EVCS' },
		nowMs: Date.parse(decision.ts)
	});
	const item = screen.getByRole('listitem');
	expect(within(item).getByText('EVCS')).toBeInTheDocument();
	expect(within(item).getByText('PV-Überschuss: Strom 8 A')).toBeInTheDocument();
	expect(within(item).queryByText(/„evcs“/)).not.toBeInTheDocument();
});

it('zeigt das Detail eines Fehlers hinter dem Befehl', () => {
	const decision = {
		...withText('Victron (ESS): Netz-Sollwert 50 W – Fehler: Modbus-Timeout.'),
		result: 'error' as const
	};
	render(WhyCard, {
		decisions: [decision],
		error: null,
		names: { victron_ess: 'Victron (ESS)' },
		nowMs: Date.parse(decision.ts)
	});
	expect(screen.getByText('Netz-Sollwert 50 W · Modbus-Timeout')).toBeInTheDocument();
	expect(screen.getByText('Fehler')).toBeInTheDocument();
});
