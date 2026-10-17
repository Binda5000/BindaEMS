import { expect, it } from 'vitest';
import { navItems } from './nav';

it('zeigt Lesenden und Bedienenden dieselben Seiten', () => {
	expect(navItems('viewer').map((item) => [item.href, item.label])).toEqual([
		['/', 'Übersicht'],
		['/laden', 'Laden'],
		['/zeitplan', 'Zeitplan'],
		['/verlauf', 'Verlauf'],
		['/statistik', 'Statistik'],
		['/verbraucher', 'Verbraucher'],
		['/system', 'System'],
		['/entscheidungen', 'Entscheidungen'],
		['/einstellungen', 'Einstellungen'],
		['/konto', 'Konto']
	]);
	expect(navItems('operator')).toEqual(navItems('viewer'));
});

it('zeigt Admins zusätzlich Benutzer und Protokoll', () => {
	expect(navItems('admin').map((item) => item.href)).toEqual([
		'/',
		'/laden',
		'/zeitplan',
		'/verlauf',
		'/statistik',
		'/verbraucher',
		'/system',
		'/entscheidungen',
		'/einstellungen',
		'/benutzer',
		'/protokoll',
		'/konto'
	]);
});

it('zeigt Laden und Entscheidungen für alle Rollen mit ihren Symbolen', () => {
	expect(navItems('viewer').map((item) => item.href)).toEqual([
		'/',
		'/laden',
		'/zeitplan',
		'/verlauf',
		'/statistik',
		'/verbraucher',
		'/system',
		'/entscheidungen',
		'/einstellungen',
		'/konto'
	]);
	const icons = Object.fromEntries(navItems('operator').map((item) => [item.href, item.icon]));
	expect(icons['/laden']).toBe('car');
	expect(icons['/entscheidungen']).toBe('list');
	expect(icons['/zeitplan']).toBe('plan');
});

it('zeigt die Statistik für alle Rollen nach dem Verlauf, mit drei Balken als Symbol', () => {
	for (const role of ['viewer', 'operator', 'admin'] as const) {
		const items = navItems(role);
		const hrefs = items.map((item) => item.href);
		expect(hrefs.indexOf('/statistik')).toBe(hrefs.indexOf('/verlauf') + 1);
		expect(items.find((item) => item.href === '/statistik')).toEqual({
			href: '/statistik',
			label: 'Statistik',
			icon: 'stats'
		});
	}
});
