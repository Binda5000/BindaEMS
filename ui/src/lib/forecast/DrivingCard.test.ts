import { fireEvent, render, screen, within } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import { driving, drivingVehicle } from '$lib/testing/fixtures';
import DrivingCard from './DrivingCard.svelte';

type Props = ComponentProps<typeof DrivingCard>;

function show(changes: Partial<Props> = {}) {
	const props: Props = {
		data: driving(),
		error: null,
		admin: true,
		onRecompute: vi.fn(),
		...changes
	};
	render(DrivingCard, props);
	return props;
}

/** Die Tabelle eines Autos, benannt nach seiner Kopfzeile */
function table(head: string): HTMLElement {
	return screen.getByRole('table', { name: head });
}

/** Die Zellen einer Zeile als Text (ohne die Kopfzeile) */
function cells(row: HTMLElement): string[] {
	return within(row)
		.getAllByRole('cell')
		.map((cell) => cell.textContent?.replace(/\s+/g, ' ').trim() ?? '');
}

/** Schaltet bei allen Autos auf „Tage einzeln“ (Standard ist gruppiert) */
async function individually(): Promise<void> {
	for (const toggle of screen.getAllByRole('button', { name: 'Tage einzeln' })) {
		await fireEvent.click(toggle);
	}
}

const TESLA = 'Tesla Model 3 · Quelle: Tessie · 56 Tage';
const EGOLF = 'e-Golf · Quelle: nur Ansteckvorgänge · 4 Tage';

it('zeigt je Auto den Kopf mit Quelle und Tagen', () => {
	show();
	expect(screen.getByRole('heading', { name: 'Fahrverhalten', level: 2 })).toBeVisible();
	expect(screen.getByRole('heading', { name: TESLA })).toBeVisible();
	expect(screen.getByRole('heading', { name: EGOLF })).toBeVisible();
});

it('zeigt je Wochentag Abfahrt, Ziel und Konfidenz', async () => {
	show();
	await individually();
	const tesla = table(TESLA);
	expect(
		within(tesla)
			.getAllByRole('columnheader')
			.map((header) => header.textContent)
	).toEqual([
		'Tag',
		'Fahrten',
		'Abfahrt',
		'Ankunft',
		'Ladestand bei Ankunft',
		'Verbrauch',
		'Vorschlag Ziel',
		'Konfidenz'
	]);
	const rows = within(tesla).getAllByRole('row').slice(1);
	expect(rows).toHaveLength(7);
	expect(cells(rows[0]!)).toEqual([
		'8 (100 %)',
		'07:15 meist 07:20',
		'17:35',
		'41 %',
		'19 %',
		'50 % (begrenzt)',
		'hoch'
	]);
	expect(within(rows[4]!).getByRole('rowheader')).toHaveTextContent('Fr');
});

it('zeigt „keine Fahrt erwartet“ und „(aus Mo–Fr)“', async () => {
	show();
	await individually();
	const tesla = within(table(TESLA)).getAllByRole('row').slice(1);
	// Samstag und Sonntag des Tesla: keine Fahrt, Werte als Strich
	expect(cells(tesla[5]!)).toEqual(['0 (0 %)', 'keine Fahrt erwartet', '–', '–', '–', '–', '–']);
	expect(within(tesla[5]!).getByRole('rowheader')).toHaveTextContent(/^Sa$/);
	// der e-Golf rechnet montags bis freitags mit der Gruppe
	const egolf = within(table(EGOLF)).getAllByRole('row').slice(1);
	expect(within(egolf[0]!).getByRole('rowheader')).toHaveTextContent('Mo (aus Mo–Fr)');
	expect(cells(egolf[0]!)[1]).toBe('08:00 meist 08:15');
	expect(cells(egolf[0]!)[6]).toBe('niedrig');
});

it('hängt die Gruppen Sa und So/Feiertag an', async () => {
	const car = drivingVehicle('egolf');
	const weekdays = car.weekdays.map((day) =>
		day.weekday >= 5 ? { ...day, basis: 'group' as const } : day
	);
	show({ data: driving({ vehicles: [{ ...car, weekdays }] }) });
	await individually();
	const rows = within(table(EGOLF)).getAllByRole('row').slice(1);
	expect(within(rows[5]!).getByRole('rowheader')).toHaveTextContent('Sa (aus Sa)');
	expect(within(rows[6]!).getByRole('rowheader')).toHaveTextContent('So (aus So/Feiertag)');
});

it('zeigt „(begrenzt)“ nur bei geklemmtem Ziel', async () => {
	const car = drivingVehicle('tesla');
	const weekdays = car.weekdays.map((day) =>
		day.weekday === 1 ? { ...day, target_clamped: false, target_soc_pct: 65 } : day
	);
	show({ data: driving({ vehicles: [{ ...car, weekdays }] }) });
	await individually();
	const rows = within(table(TESLA)).getAllByRole('row').slice(1);
	expect(cells(rows[0]!)[5]).toBe('50 % (begrenzt)');
	expect(cells(rows[1]!)[5]).toBe('65 %');
});

it('zeigt die Trefferquote oder „noch zu wenig Tage“', () => {
	show();
	expect(
		screen.getByText('Trefferquote 28 Tage: Abfahrt 89 % (19 Tage), Ziel 94 % (17 Tage)')
	).toBeVisible();
	expect(screen.getByText('Trefferquote: noch zu wenig Tage')).toBeVisible();
});

it('nennt eine Seite ohne bewertete Tage „noch zu wenig Tage“', () => {
	// Ruling T13: statt „– (0 Tage)“
	const quality = {
		days: 28,
		departure_n: 19,
		departure_hit_pct: 89,
		target_n: 0,
		target_hit_pct: null
	};
	const tesla = drivingVehicle('tesla', { quality });
	const golf = drivingVehicle('egolf', {
		quality: {
			...quality,
			departure_n: 0,
			departure_hit_pct: null,
			target_n: 17,
			target_hit_pct: 94
		}
	});
	render(DrivingCard, {
		data: driving({ vehicles: [tesla, golf] }),
		error: null,
		admin: false,
		presenceEntities: {},
		onRecompute: vi.fn()
	});
	expect(
		screen.getByText('Trefferquote 28 Tage: Abfahrt 89 % (19 Tage), Ziel – (noch zu wenig Tage)')
	).toBeVisible();
	expect(
		screen.getByText('Trefferquote 28 Tage: Abfahrt – (noch zu wenig Tage), Ziel 94 % (17 Tage)')
	).toBeVisible();
	expect(screen.queryByText(/0 Tage\)/)).toBeNull();
});

it('zeigt ein Auto ohne Tage mit „Noch keine Daten für dieses Auto.“, ohne Quelle und Tabelle', () => {
	const car = drivingVehicle('egolf', {
		source: null,
		days: 0,
		data_from: null,
		data_to: null,
		weekdays: [],
		next_trip: null,
		presence: []
	});
	show({ data: driving({ vehicles: [car] }) });
	expect(screen.getByRole('heading', { name: 'e-Golf · 0 Tage' })).toBeVisible();
	expect(screen.getByText('Noch keine Daten für dieses Auto.')).toBeVisible();
	expect(screen.queryByRole('table')).toBeNull();
	expect(screen.queryByText(/Trefferquote/)).toBeNull();
});

it('nennt bei Home Assistant die eingestellte Entität, sonst nur den Namen', () => {
	const car = drivingVehicle('tesla', { source: 'ha' });
	const data = driving({ vehicles: [car] });
	const { unmount } = render(DrivingCard, {
		data,
		error: null,
		admin: false,
		presenceEntities: { tesla: 'device_tracker.tesla' },
		onRecompute: vi.fn()
	});
	expect(
		screen.getByRole('heading', {
			name: 'Tesla Model 3 · Quelle: Home Assistant (device_tracker.tesla) · 56 Tage'
		})
	).toBeVisible();
	unmount();
	render(DrivingCard, { data, error: null, admin: false, onRecompute: vi.fn() });
	expect(
		screen.getByRole('heading', { name: 'Tesla Model 3 · Quelle: Home Assistant · 56 Tage' })
	).toBeVisible();
});

it('nennt „Home Assistant (home_entity)“ für die Entität der Konfiguration', () => {
	const car = drivingVehicle('tesla', { source: 'home_entity' });
	show({ data: driving({ vehicles: [car] }) });
	expect(
		screen.getByRole('heading', {
			name: 'Tesla Model 3 · Quelle: Home Assistant (home_entity) · 56 Tage'
		})
	).toBeVisible();
});

it('zeigt den Status des Servers wörtlich', () => {
	show({
		data: driving({
			status: { ok: false, text: 'Fahrverhalten fehlgeschlagen: HA nicht erreichbar' }
		})
	});
	expect(screen.getByText('Fahrverhalten fehlgeschlagen: HA nicht erreichbar')).toBeVisible();
	expect(screen.getByRole('img', { name: 'gestört' })).toBeVisible();
});

it('zeigt „Jetzt neu berechnen“ nur Admins und startet die Berechnung', async () => {
	const props = show();
	expect(screen.getByText('Fahrverhalten berechnet am 09.10. 03:40.')).toBeVisible();
	await fireEvent.click(screen.getByRole('button', { name: 'Jetzt neu berechnen' }));
	expect(props.onRecompute).toHaveBeenCalledTimes(1);
});

it('zeigt Nicht-Admins keine Taste', () => {
	show({ admin: false });
	expect(screen.queryByRole('button', { name: /neu berechnen|Berechnung/ })).toBeNull();
});

it('zeigt „Berechnung läuft …“ während des Laufs und sperrt die Taste', () => {
	show({ data: driving({ running: true }) });
	expect(screen.getByRole('button', { name: 'Berechnung läuft …' })).toBeDisabled();
});

it('zeigt „Berechnung läuft …“ auch beim Start und Nicht-Admins als Satz', () => {
	const { unmount } = render(DrivingCard, {
		data: driving(),
		error: null,
		admin: true,
		starting: true,
		onRecompute: vi.fn()
	});
	expect(screen.getByRole('button', { name: 'Berechnung läuft …' })).toBeDisabled();
	unmount();
	render(DrivingCard, {
		data: driving({ running: true }),
		error: null,
		admin: false,
		onRecompute: vi.fn()
	});
	expect(screen.getByText('Berechnung läuft …')).toBeVisible();
	expect(screen.queryByRole('button', { name: /neu berechnen|Berechnung/ })).toBeNull();
});

it('zeigt den Fehler der Abfrage', () => {
	show({ data: undefined, error: new ApiError(500, 'Server kaputt') });
	expect(screen.getByText('Server kaputt')).toBeVisible();
	expect(screen.queryByText('Lädt …')).toBeNull();
});

it('zeigt eine Meldung zur Taste', () => {
	show({ message: 'Start fehlgeschlagen' });
	expect(screen.getByText('Start fehlgeschlagen')).toBeVisible();
});

it('zeigt „Lädt …“, solange weder Antwort noch Fehler da sind', () => {
	show({ data: undefined });
	expect(screen.getByText('Lädt …')).toBeVisible();
});

it('fasst gleiche Tage zu einer Zeile zusammen, „Tage einzeln“ zeigt wieder alle sieben', async () => {
	show();
	const tesla = table(TESLA);
	const rows = within(tesla).getAllByRole('row').slice(1);
	expect(rows).toHaveLength(2);
	expect(within(rows[0]!).getByRole('rowheader')).toHaveTextContent(/^Mo bis Fr$/);
	expect(within(rows[1]!).getByRole('rowheader')).toHaveTextContent(/^Sa, So$/);
	expect(cells(rows[1]!)).toEqual(['0 (0 %)', 'keine Fahrt erwartet', '–', '–', '–', '–', '–']);
	const toggle = screen.getAllByRole('button', { name: 'Tage einzeln' })[0]!;
	expect(toggle).toHaveAttribute('aria-pressed', 'false');
	await fireEvent.click(toggle);
	expect(toggle).toHaveAttribute('aria-pressed', 'true');
	expect(within(table(TESLA)).getAllByRole('row').slice(1)).toHaveLength(7);
	await fireEvent.click(toggle);
	expect(within(table(TESLA)).getAllByRole('row').slice(1)).toHaveLength(2);
});

it('behält bei gruppierten Tagen den Hinweis „(aus Mo–Fr)“', () => {
	show();
	const rows = within(table(EGOLF)).getAllByRole('row').slice(1);
	expect(within(rows[0]!).getByRole('rowheader')).toHaveTextContent('Mo bis Fr (aus Mo–Fr)');
});

it('beschriftet die Zellen mit den Spaltenköpfen', () => {
	show();
	const row = within(table(TESLA)).getAllByRole('row')[1]!;
	expect(
		within(row)
			.getAllByRole('cell')
			.map((cell) => cell.getAttribute('data-label'))
	).toEqual([
		'Fahrten',
		'Abfahrt',
		'Ankunft',
		'Ladestand bei Ankunft',
		'Verbrauch',
		'Vorschlag Ziel',
		'Konfidenz'
	]);
	expect(table(TESLA)).toHaveClass('stack');
});

it('zeigt „Tage einzeln“ nur bei einem Auto, dessen Tage zusammengefasst sind', () => {
	const tesla = drivingVehicle('tesla');
	// jeder Tag mit eigener Fahrtenzahl: nichts lässt sich zusammenfassen
	const weekdays = tesla.weekdays.map((day, index) => ({ ...day, trips: index }));
	show({ data: driving({ vehicles: [{ ...tesla, weekdays }, drivingVehicle('egolf')] }) });
	expect(within(table(TESLA)).getAllByRole('row').slice(1)).toHaveLength(7);
	// nur das gruppierte e-Golf-Auto hat die Taste
	expect(screen.getAllByRole('button', { name: 'Tage einzeln' })).toHaveLength(1);
});

it('lässt „Tage einzeln“ weg, wenn kein Auto zusammengefasste Tage hat', () => {
	const tesla = drivingVehicle('tesla');
	const weekdays = tesla.weekdays.map((day, index) => ({ ...day, trips: index }));
	show({ data: driving({ vehicles: [{ ...tesla, weekdays }] }) });
	expect(screen.queryByRole('button', { name: 'Tage einzeln' })).toBeNull();
});
