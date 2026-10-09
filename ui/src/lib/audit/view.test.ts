import { expect, it } from 'vitest';
import type { AuditEntry } from '$lib/api/schemas';
import { auditRows, detailLines } from './view';

const entry = (
	action: string,
	target: string | null,
	details: AuditEntry['details']
): AuditEntry => ({
	id: 1,
	ts: '2026-10-09T08:00:00.123456+00:00',
	actor: 'admin',
	source: 'ui',
	action,
	target,
	details
});

it('beschreibt Einstellungsänderungen Feld für Feld', () => {
	const changes = [{ path: 'tariff.components.3.value_ct', old: null, new: 1.23 }];
	expect(detailLines(entry('settings.update', 'einstellungen', { version: 3, changes }))).toEqual([
		'Version 3',
		'tariff.components.3.value_ct: – → 1,23'
	]);
});

it('beschreibt Rollen und Neubewertungen', () => {
	expect(detailLines(entry('user.role', 'gast', { from: 'viewer', to: 'admin' }))).toEqual([
		'Lesen → Admin'
	]);
	expect(
		detailLines(
			entry('ledger.reprice', 'abrechnung', { from: '2026-10-01', to: '2026-10-31', slots: 2976 })
		)
	).toEqual(['01.10.2026–31.10.2026, 2976 Viertelstunden']);
});

it('filtert nach Bereich und beschriftet Aktionen', () => {
	const rows = auditRows(
		[entry('user.create', 'gast', { role: 'viewer' }), entry('consumer.delete', 'Küche', null)],
		'verbraucher'
	);
	expect(rows.map((row) => [row.action, row.target, row.who, row.when])).toEqual([
		['Verbraucher gelöscht', 'Küche', 'admin (UI)', '09.10.2026, 10:00']
	]);
});
