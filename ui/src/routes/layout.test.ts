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
