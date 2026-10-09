import { expect, it } from 'vitest';
import { nextThemePref, readThemePref, theme } from './theme.svelte';

it('liest nur gültige Werte', () => {
	expect(readThemePref({ getItem: () => 'dark' })).toBe('dark');
	expect(readThemePref({ getItem: () => 'pink' })).toBe('system');
	expect(readThemePref(null)).toBe('system');
});

it('wechselt System → Hell → Dunkel → System', () => {
	expect((['system', 'light', 'dark'] as const).map(nextThemePref)).toEqual([
		'light',
		'dark',
		'system'
	]);
});

it('setzt data-theme und merkt sich die Wahl', () => {
	theme.set('dark');
	expect([document.documentElement.dataset.theme, localStorage.getItem('bindaems.theme')]).toEqual([
		'dark',
		'dark'
	]);
	expect(theme.dark).toBe(true);
	theme.set('system');
	expect(document.documentElement.dataset.theme).toBeUndefined();
	expect(localStorage.getItem('bindaems.theme')).toBeNull();
});
