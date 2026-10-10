import { fireEvent, render } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import Layout from './+layout.svelte';

const { api, live } = vi.hoisted(() => ({
	api: { me: vi.fn(), logout: vi.fn() },
	live: {
		start: vi.fn(),
		stop: vi.fn(),
		status: 'open',
		stale: false,
		coreConnected: true,
		onUnauthorized: null
	}
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$lib/live.svelte', () => ({ live }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), afterNavigate: vi.fn() }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://ems.lan/einstellungen'), error: null, data: {} }
}));

const user = {
	id: 1,
	username: 'admin',
	role: 'admin' as const,
	totp_enabled: true,
	created_at: '2026-10-09T08:00:00+00:00'
};

afterEach(() => {
	vi.useRealTimers();
});

it('Tippen im Formular verlängert die Sitzung mit einer Anfrage im Vordergrund', async () => {
	vi.useFakeTimers({ toFake: ['Date'] });
	vi.setSystemTime(0);
	api.me.mockResolvedValue({ user });
	render(Layout, {
		data: { user },
		params: {},
		children: createRawSnippet(() => ({ render: () => '<p>Seite</p>' }))
	});
	vi.setSystemTime(61_000);
	await fireEvent.keyDown(window, { key: 'a' });
	expect(api.me).toHaveBeenCalledTimes(1);
	expect(api.me).toHaveBeenCalledWith(); // ohne Kennzeichen für Hintergrund
});
