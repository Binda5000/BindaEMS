// Texte, Reihenfolge und Gruppen der Seite „Betrieb“ (Betriebscheck, Auffälligkeiten, Schalter)
import type { OpsCheck, OpsCheckItem, OpsFinding, OpsLevel, OpsSwitch } from '$lib/api/schemas';
import { sourceLabel } from '$lib/audit/view';
import { DASH } from '$lib/format';
import { navItems } from '$lib/nav';
import { VIENNA, parseIso } from '$lib/time';

const LEVELS: Record<OpsLevel, string> = {
	ok: 'in Ordnung',
	warn: 'Warnung',
	error: 'Fehler',
	unknown: 'unbekannt'
};

/** Offene Prüfungen: erst Fehler, dann Warnungen, dann unbekannte */
const SEVERITY: Record<OpsLevel, number> = { error: 0, warn: 1, unknown: 2, ok: 3 };

export function levelLabel(level: OpsLevel): string {
	return LEVELS[level];
}

/** Stufe einer Auffälligkeit als Punkt: Warnung gelb, Hinweis als leerer Kreis */
export const FINDING_DOT: Record<OpsFinding['level'], 'warn' | 'unknown'> = {
	warn: 'warn',
	info: 'unknown'
};

export const FINDING_LABEL: Record<OpsFinding['level'], string> = {
	warn: 'Warnung',
	info: 'Hinweis'
};

function counted(count: number, one: string, many: string): string {
	return `${count} ${count === 1 ? one : many}`;
}

/** „Alles in Ordnung“ oder die Zahl der Befunde: „2 Warnungen, 1 Fehler“ (und „1 unbekannt“) */
export function checkHeadline(check: Pick<OpsCheck, 'items'>): string {
	const count = (level: OpsLevel) => check.items.filter((item) => item.level === level).length;
	const parts = [
		[count('warn'), 'Warnung', 'Warnungen'],
		[count('error'), 'Fehler', 'Fehler'],
		[count('unknown'), 'unbekannt', 'unbekannt']
	] as const;
	const found = parts.filter(([n]) => n > 0).map(([n, one, many]) => counted(n, one, many));
	return found.length > 0 ? found.join(', ') : 'Alles in Ordnung';
}

/** Die Zeile oben auf der Seite System: „Betriebscheck: alles in Ordnung“ */
export function checkLine(check: Pick<OpsCheck, 'items'>): string {
	const headline = checkHeadline(check);
	return `Betriebscheck: ${headline === 'Alles in Ordnung' ? 'alles in Ordnung' : headline}`;
}

/** Prüfungen, die nicht in Ordnung sind, nach Schwere; innerhalb einer Stufe wie vom Server */
export function openItems(items: OpsCheckItem[]): OpsCheckItem[] {
	return items
		.filter((item) => item.level !== 'ok')
		.map((item, index) => ({ item, index }))
		.sort((a, b) => SEVERITY[a.item.level] - SEVERITY[b.item.level] || a.index - b.index)
		.map(({ item }) => item);
}

/** Schalter je Gruppe; Gruppen und Schalter in der Reihenfolge des Servers (des Katalogs) */
export function groupSwitches(list: OpsSwitch[]): { group: string; items: OpsSwitch[] }[] {
	const groups: { group: string; items: OpsSwitch[] }[] = [];
	for (const item of list) {
		const found = groups.find((entry) => entry.group === item.group);
		if (found) found.items.push(item);
		else groups.push({ group: item.group, items: [item] });
	}
	return groups;
}

const SINCE = new Intl.DateTimeFormat('de-AT', {
	timeZone: VIENNA,
	day: 'numeric',
	month: 'numeric',
	year: 'numeric',
	hour: '2-digit',
	minute: '2-digit',
	hourCycle: 'h23'
});

function viennaParts(ms: number): Record<string, string> {
	return Object.fromEntries(SINCE.formatToParts(ms).map((part) => [part.type, part.value]));
}

/**
 * Seit wann und wer: „seit 3.11. 19:45 · Binda (UI)“ (Wiener Zeit; das Jahr nur, wenn es nicht
 * das von `now` ist), ohne Eintrag im Protokoll „seit Einrichtung“.
 */
export function sinceText(item: OpsSwitch, now: Date): string {
	if (item.since === null) return 'seit Einrichtung';
	const ms = parseIso(item.since);
	if (ms === null) return `seit ${DASH}`;
	const at = viennaParts(ms);
	const year = at.year === viennaParts(now.getTime()).year ? '' : at.year;
	const when = `seit ${at.day}.${at.month}.${year} ${at.hour}:${at.minute}`;
	if (item.actor === null) return when;
	return item.source === null
		? `${when} · ${item.actor}`
		: `${when} · ${item.actor} (${sourceLabel(item.source)})`;
}

/** Text eines Links nach der Seite, auf die er führt: „Entscheidungen öffnen“ */
export function linkLabel(href: string): string {
	const path = href.split(/[?#]/)[0];
	const page = navItems('admin').find((item) => item.href !== '/' && item.href === path);
	return page ? `${page.label} öffnen` : 'Öffnen';
}
