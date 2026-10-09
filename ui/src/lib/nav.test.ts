import { expect, it } from 'vitest';
import { navItems } from './nav';

it('zeigt Lesenden und Bedienenden dieselben Seiten', () => {
	expect(navItems('viewer').map((item) => [item.href, item.label])).toEqual([
		['/', 'Übersicht'],
		['/verlauf', 'Verlauf'],
		['/verbraucher', 'Verbraucher'],
		['/system', 'System'],
		['/einstellungen', 'Einstellungen'],
		['/konto', 'Konto']
	]);
	expect(navItems('operator')).toEqual(navItems('viewer'));
});

it('zeigt Admins zusätzlich Benutzer und Protokoll', () => {
	expect(navItems('admin').map((item) => item.href)).toEqual([
		'/',
		'/verlauf',
		'/verbraucher',
		'/system',
		'/einstellungen',
		'/benutzer',
		'/protokoll',
		'/konto'
	]);
});
