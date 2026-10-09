import { describe, expect, it } from 'vitest';
import error422 from './contract/error-422.json';
import { detailText, fieldErrors } from './errors';

describe('Fehlertexte', () => {
	it('übernimmt Texte des Servers', () => {
		expect(detailText('Benutzername oder Passwort falsch')).toBe(
			'Benutzername oder Passwort falsch'
		);
	});

	it('übersetzt Fehlerlisten', () => {
		const detail = [
			{
				type: 'float_parsing',
				loc: ['body', 'settings', 'tariff', 'components', 2, 'value_ct'],
				msg: 'Input should be a valid number, unable to parse string as a number',
				input: 'abc'
			},
			{ type: 'value_error', loc: ['body', 'name'], msg: 'Value error, Name fehlt', input: '' },
			{ type: 'missing', loc: ['body', 'role'], msg: 'Field required', input: {} }
		];
		expect(detailText(detail)).toBe(
			'settings.tariff.components.2.value_ct: Zahl erwartet\nname: Name fehlt\nrole: Angabe fehlt'
		);
		expect(fieldErrors(detail, ['body', 'settings'])).toEqual({
			'tariff.components.2.value_ct': 'Zahl erwartet'
		});
	});

	it('versteht die echte 422-Antwort', () => {
		expect(detailText(error422.detail)).toContain('Zahl erwartet');
	});

	it('fällt bei Unbekanntem auf einen allgemeinen Text zurück', () => {
		expect(detailText(undefined)).toBe('Unbekannter Fehler');
	});
});
