import { render } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { expect, it, vi } from 'vitest';
import Layout from './+layout.svelte';

// Verdrahtung im Rahmen: 401 einer Anfrage und 4401 der Live-Verbindung führen zur Anmeldung
const { handlers, live, goto } = vi.hoisted(() => ({
	handlers: { unauthorized: null as (() => void) | null },
	live: {
		start: vi.fn(),
		stop: vi.fn(),
		status: 'open',
		stale: false,
		coreConnected: true,
		onUnauthorized: null as (() => void) | null
	},
	goto: vi.fn()
}));
vi.mock('$lib/api/client', async (original) => ({
	...(await original<typeof import('$lib/api/client')>()),
	onUnauthorized: (handler: (() => void) | null) => (handlers.unauthorized = handler)
}));
vi.mock('$lib/live.svelte', () => ({ live }));
vi.mock('$app/navigation', () => ({ goto, afterNavigate: vi.fn() }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://ems.lan/verlauf?reihen=grid'), error: null, data: {} }
}));

const user = {
	id: 1,
	username: 'admin',
	role: 'admin' as const,
	totp_enabled: true,
	created_at: '2026-10-09T08:00:00+00:00'
};
const expired = '/login?next=%2Fverlauf%3Freihen%3Dgrid&abgelaufen=1';

function renderLayout() {
	render(Layout, {
		data: { user },
		params: {},
		children: createRawSnippet(() => ({ render: () => '<p>Seite</p>' }))
	});
}

it('401 einer Anfrage führt zur Anmeldung mit „abgelaufen“ und Rückweg', () => {
	renderLayout();
	handlers.unauthorized?.();
	expect(goto).toHaveBeenLastCalledWith(expired, { replaceState: true });
});

it('4401 der Live-Verbindung führt ebenso zur Anmeldung', () => {
	goto.mockClear();
	renderLayout();
	live.onUnauthorized?.();
	expect(goto).toHaveBeenLastCalledWith(expired, { replaceState: true });
});
