import { expect, it } from 'vitest';
import { hasRole } from './roles';

const user = (role: 'admin' | 'operator' | 'viewer') => ({
	id: 1,
	username: 'x',
	role,
	totp_enabled: false,
	created_at: '2026-10-09T08:00:00+00:00'
});

it('ordnet die Rollen Lesen < Bedienen < Admin', () => {
	expect(hasRole(user('admin'), 'operator')).toBe(true);
	expect(hasRole(user('operator'), 'operator')).toBe(true);
	expect(hasRole(user('viewer'), 'operator')).toBe(false);
	expect(hasRole(null, 'viewer')).toBe(false);
});
