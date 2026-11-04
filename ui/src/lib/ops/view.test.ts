import { expect, it } from 'vitest';
import type { OpsCheck, OpsCheckItem, OpsSwitch } from '$lib/api/schemas';
import {
	checkHeadline,
	checkLine,
	groupSwitches,
	levelLabel,
	linkLabel,
	openItems,
	sinceText
} from './view';

function item(id: string, level: OpsCheckItem['level']): OpsCheckItem {
	return { id, label: id, level, text: `${id} Text`, link: null };
}

/** Gesamtstufe wie der Server: Fehler vor Warnung, „unbekannt“ allein bleibt „ok“ */
function check(...levels: OpsCheckItem['level'][]): OpsCheck {
	return {
		level: levels.includes('error') ? 'error' : levels.includes('warn') ? 'warn' : 'ok',
		checked_at: '2026-11-04T08:00:00+00:00',
		items: levels.map((level, index) => item(`p${index}`, level))
	};
}

function switchOf(changes: Partial<OpsSwitch> = {}): OpsSwitch {
	return {
		id: 'grid_charge',
		label: 'Netzladen des Akkus',
		group: 'Akku',
		value: 'an',
		factory: 'aus',
		changed: true,
		since: '2026-11-03T18:45:00Z',
		actor: 'Binda',
		source: 'ui',
		link: '/zeitplan',
		...changes
	};
}

const NOW = new Date('2026-11-04T09:00:00Z');

it('benennt die Stufen des Betriebschecks deutsch', () => {
	expect(levelLabel('ok')).toBe('in Ordnung');
	expect(levelLabel('warn')).toBe('Warnung');
	expect(levelLabel('error')).toBe('Fehler');
	expect(levelLabel('unknown')).toBe('unbekannt');
});

it('fasst den Betriebscheck in einer Zeile zusammen', () => {
	expect(checkHeadline(check('ok', 'ok'))).toBe('Alles in Ordnung');
	expect(checkHeadline(check('warn', 'ok', 'error', 'warn'))).toBe('2 Warnungen, 1 Fehler');
	expect(checkHeadline(check('warn'))).toBe('1 Warnung');
	expect(checkHeadline(check('error', 'error'))).toBe('2 Fehler');
	expect(checkHeadline(check('unknown', 'warn'))).toBe('1 Warnung, 1 unbekannt');
});

it('nennt bei Stufe ok „Alles in Ordnung“, auch mit unbekannten Prüfungen', () => {
	expect(checkHeadline(check('ok', 'unknown'))).toBe('Alles in Ordnung');
	expect(checkLine(check('unknown'))).toBe('Betriebscheck: alles in Ordnung');
});

it('nennt den Betriebscheck auf der Seite System mit kleinem „alles“', () => {
	expect(checkLine(check('ok'))).toBe('Betriebscheck: alles in Ordnung');
	expect(checkLine(check('warn', 'error'))).toBe('Betriebscheck: 1 Warnung, 1 Fehler');
});

it('stellt offene Prüfungen nach Schwere voran, die Reihenfolge des Servers bleibt je Stufe', () => {
	const items = check('warn', 'ok', 'unknown', 'error', 'warn').items;
	expect(openItems(items).map((entry) => [entry.id, entry.level])).toEqual([
		['p3', 'error'],
		['p0', 'warn'],
		['p4', 'warn'],
		['p2', 'unknown']
	]);
});

it('gruppiert die Schalter in der Reihenfolge des Servers', () => {
	const list = [
		switchOf({ id: 'control', group: 'Regelung' }),
		switchOf({ id: 'grid_charge', group: 'Akku' }),
		switchOf({ id: 'actuator.victron', group: 'Regelung' }),
		switchOf({ id: 'report', group: 'Benachrichtigungen' })
	];
	expect(
		groupSwitches(list).map(({ group, items }) => [group, items.map((entry) => entry.id)])
	).toEqual([
		['Regelung', ['control', 'actuator.victron']],
		['Akku', ['grid_charge']],
		['Benachrichtigungen', ['report']]
	]);
	expect(groupSwitches([])).toEqual([]);
});

it('nennt seit wann und wer einen Schalter gestellt hat (Wiener Zeit)', () => {
	expect(sinceText(switchOf(), NOW)).toBe('seit 3.11. 19:45 · Binda (UI)');
	expect(sinceText(switchOf({ actor: 'automation', source: 'ha' }), NOW)).toBe(
		'seit 3.11. 19:45 · automation (Home Assistant)'
	);
	expect(sinceText(switchOf({ since: '2026-07-01T05:05:00Z', source: 'system' }), NOW)).toBe(
		'seit 1.7. 07:05 · Binda (System)'
	);
	expect(sinceText(switchOf({ actor: null, source: null }), NOW)).toBe('seit 3.11. 19:45');
});

it('nennt das Jahr, wenn die Änderung nicht aus diesem Jahr stammt', () => {
	expect(sinceText(switchOf({ since: '2025-12-31T22:30:00Z', source: 'cli' }), NOW)).toBe(
		'seit 31.12.2025 23:30 · Binda (Befehlszeile)'
	);
});

it('ohne Eintrag im Protokoll gilt der Schalter seit der Einrichtung', () => {
	expect(sinceText(switchOf({ since: null, actor: null, source: null }), NOW)).toBe(
		'seit Einrichtung'
	);
});

it('beschriftet Links mit der Seite, auf die sie führen', () => {
	expect(linkLabel('/entscheidungen?range=2026-11-03&actuator=victron')).toBe(
		'Entscheidungen öffnen'
	);
	expect(linkLabel('/zeitplan')).toBe('Zeitplan öffnen');
	expect(linkLabel('/system')).toBe('System öffnen');
	expect(linkLabel('/unbekannt')).toBe('Öffnen');
});
