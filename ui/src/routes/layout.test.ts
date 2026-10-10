import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';

const { me } = vi.hoisted(() => ({ me: vi.fn() })); // vi.mock wird an den Dateianfang gehoben
vi.mock('$lib/api/endpoints', () => ({ api: { me } }));
const { load } = await import('./+layout');

it('leitet ohne Sitzung zur Anmeldung und merkt sich das Ziel', async () => {
	me.mockRejectedValueOnce(new ApiError(401, 'Nicht angemeldet'));
	const url = new URL('https://ems.lan/system');
	await expect(load({ url } as never)).rejects.toMatchObject({
		status: 307,
		location: '/login?next=%2Fsystem'
	});
});

it('fragt auf der Anmeldeseite nicht nach dem Benutzer', async () => {
	me.mockClear();
	await expect(load({ url: new URL('https://ems.lan/login') } as never)).resolves.toEqual({
		user: null
	});
	expect(me).not.toHaveBeenCalled();
});

it('behält bei Netzfehlern und 5xx den zuletzt bekannten Benutzer', async () => {
	const user = { id: 1, username: 'admin', role: 'admin', totp_enabled: true, created_at: 'x' };
	me.mockResolvedValueOnce({ user });
	await expect(load({ url: new URL('https://ems.lan/') } as never)).resolves.toEqual({ user });
	me.mockRejectedValueOnce(new ApiError(502, 'Server nicht erreichbar (HTTP 502)'));
	await expect(load({ url: new URL('https://ems.lan/verlauf') } as never)).resolves.toEqual({
		user
	});
	me.mockRejectedValueOnce(new ApiError(0, 'Keine Verbindung zum Server'));
	await expect(load({ url: new URL('https://ems.lan/system') } as never)).resolves.toEqual({
		user
	});
	me.mockRejectedValueOnce(new ApiError(401, 'Nicht angemeldet'));
	await expect(load({ url: new URL('https://ems.lan/system') } as never)).rejects.toMatchObject({
		status: 307
	});
});
