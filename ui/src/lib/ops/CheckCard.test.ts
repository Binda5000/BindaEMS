import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { OpsCheck } from '$lib/api/schemas';
import CheckCard from './CheckCard.svelte';

const CHECK: OpsCheck = {
	level: 'error',
	checked_at: '2026-11-04T08:00:00+00:00',
	items: [
		{ id: 'core', label: 'core', level: 'ok', text: 'core verbunden', link: null },
		{
			id: 'victron_rate',
			label: 'Victron-Entscheidungen',
			level: 'warn',
			text: '40 Entscheidungen je Stunde, mehr als erwartet',
			link: '/entscheidungen?range=hour&actuator=victron'
		},
		{ id: 'alarms', label: 'Alarme', level: 'ok', text: 'keine Alarme', link: null },
		{
			id: 'battery',
			label: 'Akku-Sollwert',
			level: 'error',
			text: 'Akku-Sollwert gesperrt',
			link: null
		},
		{ id: 'plan', label: 'Plan', level: 'ok', text: 'Plan gilt', link: null }
	]
};

it('zeigt Fehler und Warnungen offen, die grünen Prüfungen eingeklappt', () => {
	render(CheckCard, { check: CHECK, error: null });
	expect(screen.getByRole('heading', { level: 2, name: 'Betriebscheck' })).toBeInTheDocument();
	expect(screen.getByText('1 Warnung, 1 Fehler')).toBeInTheDocument();
	expect(screen.getByText('Akku-Sollwert gesperrt')).toBeVisible();
	expect(screen.getByText('40 Entscheidungen je Stunde, mehr als erwartet')).toBeVisible();
	const open = screen.getByRole('list', { name: 'Befunde' });
	const rows = within(open).getAllByRole('listitem');
	expect(rows.map((row) => within(row).getByRole('img').getAttribute('aria-label'))).toEqual([
		'Fehler',
		'Warnung'
	]);
	expect(within(rows[1]!).getByRole('link', { name: 'Entscheidungen öffnen' })).toHaveAttribute(
		'href',
		'/entscheidungen?range=hour&actuator=victron'
	);

	const summary = screen.getByText('3 Prüfungen in Ordnung');
	const details = summary.closest('details');
	expect(details).not.toHaveAttribute('open');
	expect(screen.getByText('core verbunden')).not.toBeVisible();
	expect(screen.getByText('Plan gilt')).not.toBeVisible();
});

it('klappt die grünen Prüfungen auf', async () => {
	render(CheckCard, { check: CHECK, error: null });
	const details = screen.getByText('3 Prüfungen in Ordnung').closest('details')!;
	details.open = true;
	expect(screen.getByText('core verbunden')).toBeVisible();
});

it('sagt „Alles in Ordnung“ und zählt eine einzelne Prüfung in der Einzahl', () => {
	render(CheckCard, {
		check: { ...CHECK, level: 'ok', items: [CHECK.items[0]!] },
		error: null
	});
	expect(screen.getByText('Alles in Ordnung')).toBeInTheDocument();
	expect(screen.getByText('1 Prüfung in Ordnung')).toBeInTheDocument();
	expect(screen.queryByRole('list', { name: 'Befunde' })).toBeNull();
});

it('zeigt einen Fehler der Abfrage', () => {
	render(CheckCard, { check: undefined, error: new ApiError(502, 'core nicht erreichbar') });
	expect(screen.getByText('core nicht erreichbar')).toBeInTheDocument();
});
