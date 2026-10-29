import { expect, it } from 'vitest';
import {
	decisionAgeS,
	decisionQuery,
	filtersFromParams,
	filtersToParams,
	rangeQuery,
	resultLabel,
	splitName,
	whyBody
} from './view';

it('heute beginnt um 0 Uhr Wiener Zeit', () => {
	expect(rangeQuery('today', new Date('2026-10-09T08:00:00Z')).since).toBe(
		'2026-10-08T22:00:00.000Z'
	);
});

it('heute folgt der Sommerzeit: 0 Uhr Wien im Winter ist 23 Uhr UTC', () => {
	const query = rangeQuery('today', new Date('2026-11-09T08:00:00Z'));
	expect(query).toEqual({ since: '2026-11-08T23:00:00.000Z' });
});

it('letzte Stunde und 7 Tage zählen von jetzt zurück und haben kein Ende', () => {
	const now = new Date('2026-10-09T08:00:00Z');
	expect(rangeQuery('hour', now)).toEqual({ since: '2026-10-09T07:00:00.000Z' });
	expect(rangeQuery('week', now)).toEqual({ since: '2026-10-02T08:00:00.000Z' });
});

it('ein eigenes Datum ist der Wiener Tag von 0 bis 24 Uhr', () => {
	expect(rangeQuery({ date: '2026-10-08' }, new Date('2026-10-09T08:00:00Z'))).toEqual({
		since: '2026-10-07T22:00:00.000Z',
		until: '2026-10-08T22:00:00.000Z'
	});
});

it('benennt die Ergebnisse deutsch', () => {
	expect(
		(
			['ok', 'error', 'timeout', 'not_sent', 'dry_run', 'rejected', 'deferred', 'foreign'] as const
		).map(resultLabel)
	).toEqual([
		'bestätigt',
		'Fehler',
		'nicht bestätigt',
		'nicht gesendet',
		'Dry-Run',
		'abgelehnt',
		'zurückgestellt',
		'von außen geändert'
	]);
});

it('liest die Filter aus der URL und lässt Unsinn auf den Standard fallen', () => {
	expect(filtersFromParams(new URLSearchParams())).toEqual({
		range: 'today',
		actuator: '',
		result: '',
		dryRun: true,
		text: ''
	});
	expect(
		filtersFromParams(
			new URLSearchParams(
				'range=2026-10-08&actuator=vehicle.tesla&result=foreign&dry_run=false&q=Strom'
			)
		)
	).toEqual({
		range: '2026-10-08',
		actuator: 'vehicle.tesla',
		result: 'foreign',
		dryRun: false,
		text: 'Strom'
	});
	expect(
		filtersFromParams(new URLSearchParams('range=gestern&result=kaputt&dry_run=vielleicht'))
	).toEqual({ range: 'today', actuator: '', result: '', dryRun: true, text: '' });
});

it('schreibt nur Abweichungen vom Standard in die URL', () => {
	const base = filtersFromParams(new URLSearchParams());
	expect(filtersToParams(base).toString()).toBe('');
	expect(
		filtersToParams({
			...base,
			range: 'week',
			actuator: 'vehicle.tesla',
			dryRun: false,
			text: 'ä b'
		}).toString()
	).toBe('range=week&actuator=vehicle.tesla&dry_run=false&q=%C3%A4+b');
});

it('baut die Abfrage mit 100 je Seite und blendet Dry-Run nur bei „aus“ aus', () => {
	const now = new Date('2026-10-09T08:00:00Z');
	const base = filtersFromParams(new URLSearchParams());
	expect(decisionQuery(base, now)).toEqual({ since: '2026-10-08T22:00:00.000Z', limit: 100 });
	expect(
		decisionQuery(
			{ range: 'hour', actuator: 'vehicle.tesla', result: 'ok', dryRun: false, text: 'Strom' },
			now
		)
	).toEqual({
		since: '2026-10-09T07:00:00.000Z',
		actuator: 'vehicle.tesla',
		result: 'ok',
		dry_run: false,
		text: 'Strom',
		limit: 100
	});
});

it('das Alter einer Entscheidung ist nie negativ', () => {
	const now = Date.UTC(2026, 9, 9, 8, 0, 0);
	expect(decisionAgeS('2026-10-09T07:57:00+00:00', now)).toBe(180);
	expect(decisionAgeS('2026-10-09T08:00:05+00:00', now)).toBe(0);
	expect(decisionAgeS('kaputt', now)).toBeNull();
});

it('splitName gibt den Rest nach dem Namen am Anfang zurück', () => {
	expect(
		splitName(
			'Victron (ESS) (Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run, nicht gesendet.',
			'Victron (ESS)'
		)
	).toBe('(Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run, nicht gesendet.');
	expect(splitName('Victron (ESS): Netz-Sollwert 0 W – gesendet.', 'Victron (ESS)')).toBe(
		': Netz-Sollwert 0 W – gesendet.'
	);
});

it('splitName gibt null, wenn der Text nicht mit dem Namen beginnt oder das Wort weitergeht', () => {
	expect(splitName('EVCS alt: Laden aus.', 'EVCS „evcs“')).toBeNull();
	expect(splitName('Victron2: x', 'Victron')).toBeNull();
});

it('whyBody macht aus dem Satz „Gründe: Befehl“ und lässt Ergebnis und Namen weg', () => {
	expect(
		whyBody(
			'Victron (ESS) (Überschuss): Netz-Sollwert 50 W – Dry-Run, nicht gesendet.',
			'Victron (ESS)'
		)
	).toBe('Überschuss: Netz-Sollwert 50 W');
	expect(whyBody('EVCS „evcs“: Strom 8 A – Dry-Run, nicht gesendet.', 'EVCS „evcs“')).toBe(
		'Strom 8 A'
	);
});

it('whyBody hängt Befunde mit „ · “ an', () => {
	expect(
		whyBody('Tesla Model 3 (Min+PV): Strom 20 A – abgelehnt. Grenze 16 A.', 'Tesla Model 3')
	).toBe('Min+PV: Strom 20 A · Grenze 16 A.');
});

it('whyBody lässt Texte unverändert, die nicht dem Muster folgen', () => {
	expect(whyBody('Klimaanlage wurde von außen geändert', 'Klimaanlage')).toBe(
		'wurde von außen geändert'
	);
	expect(whyBody('EVCS alt: Laden aus.', 'Victron (ESS)')).toBe('EVCS alt: Laden aus.');
});
