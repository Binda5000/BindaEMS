import { expect, it } from 'vitest';
import error422 from '$lib/api/contract/error-422.json';
import { settingsCurrent } from '$lib/testing/fixtures';
import {
	applyDraft,
	defaultRepriceRange,
	describeWindow,
	draftFrom,
	editorFieldErrors,
	parseDecimal,
	validityText
} from './model';

const settings = () => settingsCurrent().settings;

it('liest deutsche Dezimalzahlen', () => {
	expect(parseDecimal('7,3')).toBe(7.3);
	expect(parseDecimal(' 8.11 ')).toBe(8.11);
	expect(parseDecimal('')).toBeNull();
	expect(parseDecimal('abc')).toBe('invalid');
	expect(parseDecimal('1,2,3')).toBe('invalid');
});

it('ändert ohne Eingaben nichts', () => {
	const current = settings();
	expect(applyDraft(current, draftFrom(current))).toEqual({
		settings: current,
		tariffChanged: false
	});
});

it('übernimmt einen Tarifwert mit Komma', () => {
	const current = settings();
	const draft = draftFrom(current);
	draft.values.grid_loss = '1,23';
	const result = applyDraft(current, draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.tariffChanged).toBe(true);
	expect(result.settings.tariff.components.find((c) => c.id === 'grid_loss')?.value_ct).toBe(1.23);
});

it('meldet ungültige Werte und doppelte Monate', () => {
	const current = settings();
	const draft = draftFrom(current);
	draft.values.grid_loss = 'viel';
	draft.feedIn = [
		{ month: '2026-09', ct: '7,3' },
		{ month: '2026-09', ct: '8' },
		{ month: '2026-10', ct: '-1' }
	];
	expect(applyDraft(current, draft)).toEqual({
		errors: {
			'values.grid_loss': 'Zahl, z. B. 8,11',
			'feedIn.1.month': 'Monat doppelt',
			'feedIn.2.ct': 'Wert ab 0'
		}
	});
});

it('beschreibt Zeitfenster und Gültigkeit', () => {
	const gridUsage = settings().tariff.components.find((c) => c.id === 'grid_usage');
	if (!gridUsage) throw new Error('grid_usage fehlt');
	expect(describeWindow(gridUsage.windows[0])).toBe('Apr–Sep, Mo–So, 10:00–16:00: × 0,8');
	expect(validityText({ ...gridUsage, valid_from: null, valid_until: null })).toBe('immer');
	expect(validityText({ ...gridUsage, valid_from: '2027-01-01', valid_until: null })).toBe(
		'ab 01.01.2027'
	);
	expect(validityText({ ...gridUsage, valid_from: null, valid_until: '2026-12-31' })).toBe(
		'bis 31.12.2026'
	);
});

it('ordnet Serverfehler dem Feld zu', () => {
	expect(editorFieldErrors(error422.detail, settings())).toEqual({
		'values.grid_usage': 'Zahl erwartet'
	});
});

it('schlägt die Neubewertung ab Monatsbeginn vor', () => {
	expect(defaultRepriceRange(new Date('2026-10-09T08:00:00Z'))).toEqual({
		first: '2026-10-01',
		last: '2026-10-09'
	});
});
