import * as v from 'valibot';
import { expect, it } from 'vitest';
import { SystemResponseSchema } from '$lib/api/schemas';
import { system } from '$lib/testing/fixtures';
import { noticesFrom } from './notices';

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

it('ohne Systemdaten gibt es keine Hinweise', () => {
	expect(noticesFrom(undefined)).toEqual([]);
});
