// Rollen: Lesen < Bedienen < Admin (Spec 14)
import { error } from '@sveltejs/kit';
import type { Role, User } from './api/schemas';

export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
export const ROLE_LABELS: Record<Role, string> = {
	admin: 'Admin',
	operator: 'Bedienen',
	viewer: 'Lesen'
};

export function hasRole(user: User | null | undefined, role: Role): boolean {
	return user != null && ROLE_RANK[user.role] >= ROLE_RANK[role];
}

/** Für Seiten nur für Admins: sonst die Fehlerseite „Keine Berechtigung“. */
export function requireAdmin(user: User | null | undefined): void {
	if (!hasRole(user, 'admin')) error(403, 'Keine Berechtigung');
}
