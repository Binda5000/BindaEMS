import * as v from 'valibot';
import { expect, it } from 'vitest';
import { SystemResponseSchema } from '$lib/api/schemas';
import { system } from '$lib/testing/fixtures';
import { noticeSummary, noticesFrom } from './notices';

const since = '2026-10-09T08:00:00+00:00';

it('ordnet Fehler vor Warnungen und entfernt Doppeltes', () => {
	const base = system();
	const health = base.core.health;
	const data = v.parse(SystemResponseSchema, {
		core: {
			connected: true,
			health: health && {
				...health,
				alarms: [
					{ id: 'competitor.dess', severity: 'warning', message: 'Dynamic ESS regelt mit', since },
					{ id: 'plaus.soc', severity: 'error', message: 'SOC unplausibel', since }
				]
			}
		},
		components: [
			{
				name: 'prices',
				ok: false,
				message: 'Kein Preis für den aktuellen Slot',
				since: null,
				details: {}
			},
			{ name: 'influx', ok: true, message: 'InfluxDB: alles übertragen', since: null, details: {} }
		],
		warnings: ['Noch kein OeMAG-Monatswert eingetragen.', 'Noch kein OeMAG-Monatswert eingetragen.']
	});
	expect(noticesFrom(data)).toEqual([
		{ level: 'error', text: 'SOC unplausibel' },
		{ level: 'warning', text: 'Dynamic ESS regelt mit' },
		{ level: 'warning', text: 'Kein Preis für den aktuellen Slot' },
		{ level: 'warning', text: 'Noch kein OeMAG-Monatswert eingetragen.' }
	]);
});

it('macht aus `info` Hinweise der Stufe „info“, nach Warnungen', () => {
	const text = 'PV-Prognose gekürzt: vermutlich Schnee auf den Modulen (seit 07:15)';
	const base = system();
	const data = { ...base, core: { ...base.core, health: null }, components: [] };
	expect(noticesFrom({ ...data, warnings: ['W'], info: [text] })).toEqual([
		{ level: 'warning', text: 'W' },
		{ level: 'info', text }
	]);
	// ältere Stände ohne `info`
	const older: Record<string, unknown> = { ...base };
	delete older.info;
	expect(v.parse(SystemResponseSchema, older).info).toEqual([]);
});

it('ohne Systemdaten gibt es keine Hinweise', () => {
	expect(noticesFrom(undefined)).toEqual([]);
});

it('fasst Hinweise zusammen: Anzahl, die ersten zwei Texte, schwerste Stufe', () => {
	expect(noticeSummary([])).toBeNull();
	expect(noticeSummary([{ level: 'info', text: 'Nur ein Hinweis' }])).toEqual({
		count: '1 Hinweis',
		preview: 'Nur ein Hinweis',
		level: 'info'
	});
	expect(
		noticeSummary([
			{ level: 'info', text: 'A' },
			{ level: 'error', text: 'B' },
			{ level: 'warning', text: 'C' }
		])
	).toEqual({ count: '3 Hinweise', preview: 'A · B', level: 'error' });
	expect(
		noticeSummary([
			{ level: 'info', text: 'A' },
			{ level: 'warning', text: 'B' }
		])?.level
	).toBe('warning');
});
