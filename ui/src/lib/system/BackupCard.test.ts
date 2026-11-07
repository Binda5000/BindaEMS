import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { BACKUP, OUTAGE } from '$lib/testing/backup';
import BackupCard from './BackupCard.svelte';

function text(label: string): string {
	return (screen.getByText(label).nextElementSibling?.textContent ?? '').replace(/\s/g, ' ').trim();
}

it('zeigt Reichweite, Lastannahme, Untergrenze und Lebenszeichen', () => {
	render(BackupCard, { data: BACKUP, error: null });
	expect(screen.getByText('USV und Netzausfälle')).toBeTruthy();
	expect(text('Reichweite jetzt')).toBe('≈ 31 h');
	expect(text('Reichweite ab Reserve')).toBe('≈ 14 h');
	expect(text('Last an AC-Out')).toBe('≈ 712 W (Mittel 28.10.–3.11.), nachts ≈ 551 W');
	expect(text('Untergrenze')).toBe('5 %');
	expect(text('Lebenszeichen')).toMatch(/^eingerichtet, zuletzt 14:05: in Ordnung$/);
	expect(screen.getByText(/ohne PV/)).toBeTruthy();
	expect(screen.getByText('Noch kein Netzausfall erfasst.')).toBeTruthy();
});

it('nennt bei laufendem Ausfall dessen Mittel als Lastannahme', () => {
	render(BackupCard, { data: { ...BACKUP, load_source: 'ausfall', load_w: 1180 }, error: null });
	expect(text('Last an AC-Out')).toMatch(/^≈ 1,18 kW \(Mittel des Ausfalls\)/);
});

it('meldet ein nicht eingerichtetes Lebenszeichen', () => {
	render(BackupCard, {
		data: { ...BACKUP, ping: { configured: false, ok: true, last_sent: null, text: null } },
		error: null
	});
	expect(text('Lebenszeichen')).toBe('nicht eingerichtet');
});

it('zeigt die Netzausfälle als Tabelle', () => {
	render(BackupCard, {
		data: {
			...BACKUP,
			outages: [OUTAGE, { ...OUTAGE, end_estimated: true, mean_w: null, soc_end: null }]
		},
		error: null
	});
	const rows = within(screen.getByRole('table')).getAllByRole('row');
	expect(rows).toHaveLength(3);
	const cells = within(rows[1]!)
		.getAllByRole('cell')
		.map((cell) => cell.textContent?.replace(/\s/g, ' ').trim());
	expect(cells).toEqual(['03.11.2026, 14:03', '14 min', '62 → 60,5 % (min 60,5 %)', '≈ 1,18 kW']);
	const second = within(rows[2]!).getAllByRole('cell');
	expect(second[1]?.textContent).toMatch(/14\smin/);
	expect(second[1]?.textContent).toContain('Ende geschätzt');
	expect(second[3]?.textContent).toBe('–');
});

it('weist auf einen laufenden Netzausfall hin', () => {
	render(BackupCard, {
		data: {
			...BACKUP,
			outage: { start: OUTAGE.start, soc_start: 62, mean_w: 1180 },
			outages: [{ ...OUTAGE, end: null, duration_s: 1000 }]
		},
		error: null
	});
	expect(screen.getByText(/^Netzausfall seit 03\.11\.2026, 14:03/)).toBeTruthy();
	expect(within(screen.getByRole('table')).getByText(/läuft/)).toBeTruthy();
});

it('zeigt einen Fehler der Abfrage', () => {
	render(BackupCard, { data: undefined, error: { detail: 'Nicht erreichbar' } as never });
	expect(screen.getByText('Nicht erreichbar')).toBeTruthy();
});

it('meldet zu wenig Daten mit der Zahl der Viertelstunden, wenn keine Last bekannt ist', () => {
	render(BackupCard, {
		data: {
			...BACKUP,
			now_h: null,
			reserve_h: null,
			night_h: null,
			load_w: null,
			load_source: null,
			outage: null,
			basis: { ...BACKUP.basis!, day_slots: 96 }
		},
		error: null
	});
	expect(text('Reichweite jetzt')).toBe('zu wenig Daten');
	expect(text('Reichweite ab Reserve')).toBe('zu wenig Daten');
	expect(text('Reichweite nachts')).toBe('zu wenig Daten');
	expect(text('Last an AC-Out')).toBe('zu wenig Daten (96 von 192 Viertelstunden)');
});

it('meldet zu wenig Daten ohne Zahl, wenn es noch keine Grundlage gibt', () => {
	render(BackupCard, {
		data: {
			...BACKUP,
			now_h: null,
			reserve_h: null,
			night_h: null,
			load_w: null,
			load_source: null,
			outage: null,
			basis: null
		},
		error: null
	});
	expect(text('Last an AC-Out')).toBe('zu wenig Daten');
});
