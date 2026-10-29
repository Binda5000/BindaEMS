import { fireEvent, render, screen, within } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import { quality } from '$lib/testing/fixtures';
import QualityTable from './QualityTable.svelte';

type Props = ComponentProps<typeof QualityTable>;

function show(changes: Partial<Props> = {}) {
	const props: Props = { data: quality(), error: null, days: 14, onSelect: vi.fn(), ...changes };
	render(QualityTable, props);
	return props;
}

it('sagt in einem Satz, wie viel näher das Modell liegt', () => {
	show();
	expect(
		screen.getByText('Das Lastmodell lag im Mittel 29 % näher am echten Verbrauch als das Profil.')
	).toBeInTheDocument();
});

it('vergleicht Profil, Modell und verwendete Last je Vorlauf', () => {
	show();
	const table = screen.getAllByRole('table')[0]!;
	for (const name of ['Vorlauf', 'Profil', 'Modell', 'im Plan']) {
		expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument();
	}
	const row = within(table).getByRole('row', { name: /^bis 6 h/ });
	expect(
		within(row)
			.getAllByRole('cell')
			.map((c) => c.textContent?.trim())
	).toEqual(['bis 6 h', '115 W', '82 W', '113 W']);
	expect(
		screen.getByText('Mittlere Abweichung je Viertelstunde, kleiner ist besser.')
	).toBeInTheDocument();
	expect(
		screen.getByText(/^PV-Prognose, bis 6 h: 240\sW \(4 Viertelstunden\)$/)
	).toBeInTheDocument();
});

it('hält die alte Tabelle in „Alle Werte“, zunächst geschlossen', () => {
	show();
	const details = screen.getByText('Alle Werte').closest('details')!;
	expect(details.open).toBe(false);
	const table = within(details).getByRole('table');
	expect(table).toHaveClass('stack');
	expect(within(table).getByRole('columnheader', { name: 'Viertelstunden' })).toBeInTheDocument();
	expect(within(table).queryByRole('columnheader', { name: 'Slots' })).toBeNull();
	expect(within(table).getAllByRole('row')).toHaveLength(1 + 10);
	for (const cell of within(table).getAllByRole('cell')) {
		const first = cell.parentElement?.firstElementChild === cell;
		expect(first || cell.hasAttribute('data-label')).toBe(true);
	}
});

it('wählt den Zeitraum über die Segment-Leiste', async () => {
	const props = show();
	expect(screen.getByRole('button', { name: '14 Tage' })).toHaveAttribute('aria-pressed', 'true');
	await fireEvent.click(screen.getByRole('button', { name: '28 Tage' }));
	expect(props.onSelect).toHaveBeenCalledExactlyOnceWith(28);
});

it('zeigt ohne Werte den Hinweis und bei Fehler die Meldung', () => {
	show({ data: quality({ rows: [] }) });
	expect(screen.getByText('Noch keine Werte im Zeitraum.')).toBeInTheDocument();
	expect(screen.queryByText('Alle Werte')).toBeNull();
});

it('meldet einen Fehler', () => {
	show({ data: undefined, error: new ApiError(500, 'kaputt') });
	expect(screen.getByText('kaputt')).toBeInTheDocument();
});
