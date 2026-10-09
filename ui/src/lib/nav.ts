// Seiten der App in der Reihenfolge der Navigation; Benutzer und Protokoll nur für Admins
import type { Role } from './api/schemas';

export type IconName =
	| 'overview'
	| 'history'
	| 'consumers'
	| 'system'
	| 'settings'
	| 'users'
	| 'audit'
	| 'account'
	| 'menu'
	| 'theme'
	| 'pv'
	| 'grid'
	| 'battery'
	| 'house'
	| 'wallbox'
	| 'car';

export interface NavItem {
	href: string;
	label: string;
	icon: IconName;
}

const PAGES: readonly (NavItem & { adminOnly?: boolean })[] = [
	{ href: '/', label: 'Übersicht', icon: 'overview' },
	{ href: '/verlauf', label: 'Verlauf', icon: 'history' },
	{ href: '/verbraucher', label: 'Verbraucher', icon: 'consumers' },
	{ href: '/system', label: 'System', icon: 'system' },
	{ href: '/einstellungen', label: 'Einstellungen', icon: 'settings' },
	{ href: '/benutzer', label: 'Benutzer', icon: 'users', adminOnly: true },
	{ href: '/protokoll', label: 'Protokoll', icon: 'audit', adminOnly: true },
	{ href: '/konto', label: 'Konto', icon: 'account' }
];

export function navItems(role: Role): NavItem[] {
	return PAGES.filter((item) => !item.adminOnly || role === 'admin').map(
		({ href, label, icon }) => ({ href, label, icon })
	);
}

/** Gehört `pathname` zur Seite `href` (Unterseiten eingeschlossen)? */
export function isCurrent(href: string, pathname: string): boolean {
	return href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);
}
