// Beispielantwort von GET /api/backup für die Tests von Kachel und Karte
import type { Backup, BackupOutage } from '$lib/api/schemas';

export const OUTAGE: BackupOutage = {
	start: '2026-11-03T13:03:00+00:00',
	end: '2026-11-03T13:17:00+00:00',
	end_estimated: false,
	duration_s: 840,
	soc_start: 62,
	soc_end: 60.5,
	soc_min: 60.5,
	mean_w: 1180,
	load_kwh: 0.28
};

export const BACKUP: Backup = {
	now_h: 31.4,
	reserve_h: 14.1,
	night_h: 40.3,
	load_w: 712,
	load_source: 'tag',
	floor_soc_pct: 5,
	basis: {
		first_day: '2026-10-28',
		last_day: '2026-11-03',
		day_slots: 672,
		night_slots: 140,
		night_w: 551
	},
	outage: null,
	outages: [],
	ping: { configured: true, ok: true, last_sent: '2026-11-03T13:05:00+00:00', text: 'in Ordnung' }
};
