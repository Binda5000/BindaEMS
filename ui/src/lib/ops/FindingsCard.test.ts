import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { OpsFindings } from '$lib/api/schemas';
import FindingsCard from './FindingsCard.svelte';

const FINDINGS: OpsFindings = {
	days: [
		{
			day: '2026-11-03',
			computed: true,
			findings: [
				{
					rule: 'grid_charge_unplanned',
					level: 'warn',
					start: '2026-11-03T21:30:00+00:00',
					end: '2026-11-03T22:00:00+00:00',
					text: 'Akku lud 22:30–23:00 mit bis zu 9,1 kW aus dem Netz (4,2 kWh), der Plan sah kein Netzladen vor.',
					link: '/entscheidungen?range=2026-11-03&actuator=victron',
					data: { kwh: 4.2 }
				},
				{
					rule: 'not_checked',
					level: 'info',
					start: null,
					end: null,
					text: 'Akku-SOC nicht geprüft: InfluxDB nicht erreichbar.',
					link: null,
					data: {}
				}
			]
		},
		{ day: '2026-11-02', computed: true, findings: [] },
		{ day: '2026-11-01', computed: false, findings: [] }
	],
	report: {
		day: '2026-11-03',
		text: 'Dienstag 3.11.: Netzbezug 17,4 kWh. 1 Auffälligkeit: Akku lud aus dem Netz.'
	}
};

it('zeigt oben den Bericht von gestern', () => {
	render(FindingsCard, { findings: FINDINGS, error: null });
	expect(screen.getByRole('heading', { level: 2, name: 'Auffälligkeiten' })).toBeInTheDocument();
	expect(
		screen.getByRole('heading', { level: 3, name: 'Tagesbericht über gestern' })
	).toBeInTheDocument();
	expect(
		screen.getByText('Dienstag 3.11.: Netzbezug 17,4 kWh. 1 Auffälligkeit: Akku lud aus dem Netz.')
	).toBeInTheDocument();
});

it('zeigt je Tag die Treffer mit Link, Tage ohne Treffer als „keine“', () => {
	render(FindingsCard, { findings: FINDINGS, error: null });
	const days = screen.getAllByRole('listitem').filter((row) => row.classList.contains('day'));
	expect(days).toHaveLength(3);
	expect(within(days[0]!).getByText(/Akku lud 22:30–23:00/)).toBeInTheDocument();
	expect(within(days[0]!).getByRole('img', { name: 'Warnung' })).toBeInTheDocument();
	expect(within(days[0]!).getByRole('img', { name: 'Hinweis' })).toBeInTheDocument();
	expect(within(days[0]!).getByRole('link', { name: 'Entscheidungen öffnen' })).toHaveAttribute(
		'href',
		'/entscheidungen?range=2026-11-03&actuator=victron'
	);
	expect(within(days[0]!).getAllByRole('link')).toHaveLength(1);
	expect(days[1]).toHaveTextContent(/^\s*Mo\., 02\.11\.\s*keine\s*$/);
	expect(days[2]).toHaveTextContent(/^\s*So\., 01\.11\.\s*noch nicht gerechnet\s*$/);
});

it('kommt ohne Bericht aus', () => {
	render(FindingsCard, { findings: { ...FINDINGS, report: null }, error: null });
	expect(screen.queryByRole('heading', { level: 3, name: 'Tagesbericht über gestern' })).toBeNull();
	expect(screen.getByText(/Akku lud 22:30–23:00/)).toBeInTheDocument();
});
