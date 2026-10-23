import { render, screen, within } from '@testing-library/svelte';
import { fireEvent } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import { SESSIONS_LIMIT } from '$lib/api/endpoints';
import { chargingSession } from '$lib/testing/fixtures';
import SessionsCard from './SessionsCard.svelte';
import { SESSIONS_PAGE } from './view';

const FILENAME = 'ladesessions-2026-10-01-2026-10-31-tesla.csv';

afterEach(() => {
	vi.restoreAllMocks();
	// jsdom kennt beides nicht; die Tests setzen es selbst
	Reflect.deleteProperty(URL, 'createObjectURL');
	Reflect.deleteProperty(URL, 'revokeObjectURL');
});

it('listet die Sessions, die offene als läuft', () => {
	render(SessionsCard, {
		filename: FILENAME,
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
	render(SessionsCard, { sessions: [], filename: FILENAME });
	expect(screen.getByText('Keine Ladesessions im Zeitraum.')).toBeInTheDocument();
	expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('zeigt bis zur Antwort „Lädt …“', () => {
	render(SessionsCard, { sessions: undefined, filename: FILENAME });
	expect(screen.getByText('Lädt …')).toBeInTheDocument();
});

it('zeigt den Fehler der Abfrage statt „Lädt …“', () => {
	render(SessionsCard, {
		sessions: undefined,
		filename: FILENAME,
		error: 'Sessions nicht verfügbar'
	});
	expect(screen.getByText('Sessions nicht verfügbar')).toBeInTheDocument();
	expect(screen.queryByText('Lädt …')).not.toBeInTheDocument();
});

it('zeigt zuerst 50 Sessions und lädt mit „Weitere 50 anzeigen“ nach', async () => {
	expect(SESSIONS_PAGE).toBe(50);
	const sessions = Array.from({ length: 120 }, (_, id) => chargingSession({ id }));
	render(SessionsCard, { sessions, filename: FILENAME });
	const rowCount = () =>
		within(screen.getByRole('table', { name: 'Ladesessions' })).getAllByRole('row').length - 1;
	expect(rowCount()).toBe(50);
	expect(screen.getByText('50 von 120 angezeigt')).toBeInTheDocument();

	await fireEvent.click(screen.getByRole('button', { name: 'Weitere 50 anzeigen' }));
	expect(rowCount()).toBe(100);
	expect(screen.getByText('100 von 120 angezeigt')).toBeInTheDocument();

	await fireEvent.click(screen.getByRole('button', { name: 'Weitere 50 anzeigen' }));
	expect(rowCount()).toBe(120);
	expect(screen.queryByRole('button', { name: 'Weitere 50 anzeigen' })).not.toBeInTheDocument();
	expect(screen.queryByText(/angezeigt$/)).not.toBeInTheDocument();
});

it('zeigt bei höchstens 50 Sessions weder Zähler noch Nachladen', () => {
	const sessions = Array.from({ length: 50 }, (_, id) => chargingSession({ id }));
	render(SessionsCard, { sessions, filename: FILENAME });
	expect(screen.queryByRole('button', { name: 'Weitere 50 anzeigen' })).not.toBeInTheDocument();
	expect(screen.queryByText(/angezeigt$/)).not.toBeInTheDocument();
});

it('beginnt bei anderem Zeitraum oder Auto wieder bei 50, beim Nachladen nicht', async () => {
	const many = (offset: number) =>
		Array.from({ length: 120 }, (_, id) => chargingSession({ id: offset + id }));
	const { rerender } = render(SessionsCard, { sessions: many(0), filename: FILENAME });
	await fireEvent.click(screen.getByRole('button', { name: 'Weitere 50 anzeigen' }));
	expect(screen.getByText('100 von 120 angezeigt')).toBeInTheDocument();
	// dieselbe Auswahl, neue Antwort des Servers: die Zeilen bleiben
	await rerender({ sessions: many(1000), filename: FILENAME });
	expect(screen.getByText('100 von 120 angezeigt')).toBeInTheDocument();
	// anderes Auto: wieder die ersten 50
	await rerender({ sessions: many(2000), filename: 'ladesessions-2026-10-01-2026-10-31.csv' });
	expect(screen.getByText('50 von 120 angezeigt')).toBeInTheDocument();
});

it('weist ab SESSIONS_LIMIT auf die Grenze hin', () => {
	const sessions = Array.from({ length: SESSIONS_LIMIT }, (_, id) => chargingSession({ id }));
	render(SessionsCard, { sessions, filename: FILENAME });
	expect(screen.getByText('Angezeigt werden die 2000 neuesten Sessions.')).toBeInTheDocument();
});

it('bietet „CSV herunterladen“ nur mit Sessions', () => {
	render(SessionsCard, { sessions: [], filename: FILENAME });
	expect(screen.queryByRole('button', { name: 'CSV herunterladen' })).not.toBeInTheDocument();
});

it('bietet „CSV herunterladen“ nicht vor der Antwort', () => {
	render(SessionsCard, { sessions: undefined, filename: FILENAME });
	expect(screen.queryByRole('button', { name: 'CSV herunterladen' })).not.toBeInTheDocument();
});

it('speichert mit „CSV herunterladen“ alle Sessions unter dem Dateinamen', async () => {
	const createObjectURL = vi.fn<(blob: Blob) => string>(() => 'blob:csv');
	const revokeObjectURL = vi.fn();
	Object.assign(URL, { createObjectURL, revokeObjectURL });
	const clicked: { download: string; href: string }[] = [];
	vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
		this: HTMLAnchorElement
	) {
		clicked.push({ download: this.download, href: this.getAttribute('href') ?? '' });
	});
	const sessions = Array.from({ length: 60 }, (_, id) => chargingSession({ id }));
	render(SessionsCard, { sessions, filename: FILENAME });

	await fireEvent.click(screen.getByRole('button', { name: 'CSV herunterladen' }));
	expect(createObjectURL).toHaveBeenCalledTimes(1);
	const blob = createObjectURL.mock.calls[0][0];
	expect(blob).toBeInstanceOf(Blob);
	expect(blob.type).toBe('text/csv;charset=utf-8');
	// alle 60, nicht nur die 50 angezeigten: Kopfzeile + 60 Zeilen
	const text = new TextDecoder('utf-8', { ignoreBOM: true }).decode(await blob.arrayBuffer());
	expect(text.split('\r\n')).toHaveLength(61);
	expect(clicked).toEqual([{ download: FILENAME, href: 'blob:csv' }]);
	expect(document.querySelector('a[download]')).toBeNull(); // der Link bleibt nicht im Dokument
});

it('dimmt die Karte und sperrt „CSV herunterladen“, solange die Sessions veraltet sind', async () => {
	const sessions = [chargingSession()];
	const { rerender } = render(SessionsCard, { sessions, filename: FILENAME, stale: true });
	expect(screen.getByRole('table', { name: 'Ladesessions' }).closest('.sessions')).toHaveClass(
		'stale'
	);
	expect(screen.getByRole('button', { name: 'CSV herunterladen' })).toBeDisabled();
	await rerender({ sessions, filename: FILENAME, stale: false });
	expect(screen.getByRole('table', { name: 'Ladesessions' }).closest('.sessions')).not.toHaveClass(
		'stale'
	);
	expect(screen.getByRole('button', { name: 'CSV herunterladen' })).toBeEnabled();
});

it('gibt die Download-Adresse erst verzögert frei', async () => {
	vi.useFakeTimers();
	try {
		const revokeObjectURL = vi.fn();
		Object.assign(URL, { createObjectURL: () => 'blob:csv', revokeObjectURL });
		vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		render(SessionsCard, { sessions: [chargingSession()], filename: FILENAME });
		await fireEvent.click(screen.getByRole('button', { name: 'CSV herunterladen' }));
		// sofort noch nicht: Safari und manche Firefox brechen den Download sonst ab
		expect(revokeObjectURL).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(40_000);
		expect(revokeObjectURL).toHaveBeenCalledWith('blob:csv');
	} finally {
		vi.useRealTimers();
	}
});
