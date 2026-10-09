// Änderungsprotokoll: deutsche Aktionen, Details als Zeilen, Filter nach Bereich
import type { AuditEntry } from '$lib/api/schemas';
import { DASH, formatDateTime } from '$lib/format';
import { ROLE_LABELS } from '$lib/roles';

export type AuditGroup = 'alle' | 'benutzer' | 'einstellungen' | 'verbraucher' | 'abrechnung';

export const AUDIT_GROUPS: { id: AuditGroup; label: string }[] = [
	{ id: 'alle', label: 'Alle' },
	{ id: 'benutzer', label: 'Benutzer' },
	{ id: 'einstellungen', label: 'Einstellungen' },
	{ id: 'verbraucher', label: 'Verbraucher' },
	{ id: 'abrechnung', label: 'Abrechnung' }
];

const PREFIXES: Record<Exclude<AuditGroup, 'alle'>, string> = {
	benutzer: 'user.',
	einstellungen: 'settings.',
	verbraucher: 'consumer.',
	abrechnung: 'ledger.'
};

const ACTIONS: Record<string, string> = {
	'user.create': 'Benutzer angelegt',
	'user.password': 'Passwort geändert',
	'user.role': 'Rolle geändert',
	'user.delete': 'Benutzer gelöscht',
	'user.totp_enable': 'TOTP aktiviert',
	'user.totp_disable': 'TOTP abgeschaltet',
	'settings.update': 'Einstellungen geändert',
	'consumer.create': 'Verbraucher angelegt',
	'consumer.update': 'Verbraucher geändert',
	'consumer.delete': 'Verbraucher gelöscht',
	'ledger.reprice': 'Abrechnung neu bewertet'
};

const SOURCES: Record<AuditEntry['source'], string> = {
	ui: 'UI',
	cli: 'Befehlszeile',
	ha: 'Home Assistant',
	system: 'System'
};

const NUMBER = new Intl.NumberFormat('de-AT', { maximumFractionDigits: 3 });

export function actionLabel(action: string): string {
	return ACTIONS[action] ?? action;
}

function valueText(value: unknown): string {
	if (value === null || value === undefined) return DASH;
	if (typeof value === 'number') return NUMBER.format(value);
	if (typeof value === 'boolean') return value ? 'ja' : 'nein';
	if (typeof value === 'string') return value;
	return JSON.stringify(value);
}

function roleText(value: unknown): string {
	return typeof value === 'string' && value in ROLE_LABELS
		? ROLE_LABELS[value as keyof typeof ROLE_LABELS]
		: valueText(value);
}

function dateText(value: unknown): string {
	if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return valueText(value);
	const [year, month, day] = value.split('-');
	return `${day}.${month}.${year}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function detailLines(entry: AuditEntry): string[] {
	const details = entry.details;
	if (!details) return [];
	switch (entry.action) {
		case 'settings.update': {
			const lines = typeof details.version === 'number' ? [`Version ${details.version}`] : [];
			for (const change of Array.isArray(details.changes) ? details.changes : []) {
				if (!isRecord(change)) continue;
				lines.push(
					`${valueText(change.path)}: ${valueText(change.old)} → ${valueText(change.new)}`
				);
			}
			return lines;
		}
		case 'consumer.update':
			return Object.entries(isRecord(details.changes) ? details.changes : {}).map(
				([field, change]) =>
					isRecord(change)
						? `${field}: ${valueText(change.old)} → ${valueText(change.new)}`
						: `${field}: ${valueText(change)}`
			);
		case 'user.create':
			return [`Rolle: ${roleText(details.role)}`];
		case 'user.role':
			return [`${roleText(details.from)} → ${roleText(details.to)}`];
		case 'ledger.reprice':
			return [
				`${dateText(details.from)}–${dateText(details.to)}, ${String(details.slots)} Viertelstunden`
			];
		default:
			return Object.entries(details).map(([key, value]) => `${key}: ${valueText(value)}`);
	}
}

export interface AuditRow {
	id: number;
	when: string;
	who: string;
	action: string;
	target: string;
	details: string[];
}

export function auditRows(entries: AuditEntry[], group: AuditGroup): AuditRow[] {
	const prefix = group === 'alle' ? '' : PREFIXES[group];
	return entries
		.filter((entry) => entry.action.startsWith(prefix))
		.map((entry) => ({
			id: entry.id,
			when: formatDateTime(entry.ts),
			who: `${entry.actor} (${SOURCES[entry.source]})`,
			action: actionLabel(entry.action),
			target: entry.target ?? DASH,
			details: detailLines(entry)
		}));
}
