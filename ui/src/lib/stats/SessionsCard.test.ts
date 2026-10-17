import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { chargingSession } from '$lib/testing/fixtures';
import SessionsCard from './SessionsCard.svelte';

it('listet die Sessions, die offene als läuft', () => {
	render(SessionsCard, {
		sessions: [
			chargingSession({ id: 2, open: true, end: null, wallbox_name: 'Garage' }),
			chargingSession({ id: 1 })
		]
	});
	expect(screen.getByRole('heading', { name: 'Ladesessions' })).toBeInTheDocument();
	const table = screen.getByRole('table', { name: 'Ladesessions' });
	expect(
		within(table)
			.getAllByRole('columnheader')
			.map((cell) => cell.textContent)
	).toEqual(['Beginn', 'Ende', 'Wallbox', 'Auto', 'Energie', 'PV / Akku / Netz', 'Kosten']);
	const rows = within(table).getAllByRole('row').slice(1);
	expect(rows).toHaveLength(2);
	expect(within(rows[0]).getAllByRole('cell')[1]).toHaveTextContent('läuft');
	expect(within(rows[0]).getAllByRole('cell')[2]).toHaveTextContent('Garage');
	expect(within(rows[1]).getAllByRole('cell')[1]).not.toHaveTextContent('läuft');
	expect(table.parentElement).toHaveClass('scroll');
});

it('sagt, wenn keine Session im Zeitraum lag', () => {
	render(SessionsCard, { sessions: [] });
	expect(screen.getByText('Keine Ladesessions im Zeitraum.')).toBeInTheDocument();
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('zeigt bis zur Antwort „Lädt …“', () => {
	render(SessionsCard, { sessions: undefined });
	expect(screen.getByText('Lädt …')).toBeInTheDocument();
});

it('zeigt den Fehler der Abfrage statt „Lädt …“', () => {
	render(SessionsCard, { sessions: undefined, error: 'Sessions nicht verfügbar' });
	expect(screen.getByText('Sessions nicht verfügbar')).toBeInTheDocument();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();
});

it('weist auf die Grenze von 200 Sessions hin', () => {
	const sessions = Array.from({ length: 200 }, (_, id) => chargingSession({ id }));
	render(SessionsCard, { sessions });
	expect(screen.getByText('Angezeigt werden die 200 neuesten Sessions.')).toBeInTheDocument();
});
