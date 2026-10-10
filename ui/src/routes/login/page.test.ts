import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import Page from './+page.svelte';

const { api, goto } = vi.hoisted(() => ({
	api: { login: vi.fn(), me: vi.fn() },
	goto: vi.fn()
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/navigation', () => ({ goto }));
const { state } = vi.hoisted(() => ({
	state: { url: new URL('http://192.168.82.20:8080/login?next=%2F') }
}));
vi.mock('$app/state', () => ({ page: state }));

const user = {
	id: 1,
	username: 'admin',
	role: 'admin' as const,
	totp_enabled: false,
	created_at: '2026-10-09T08:00:00+00:00'
};

async function signIn() {
	render(Page);
	await fireEvent.input(screen.getByLabelText('Benutzername'), { target: { value: 'admin' } });
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: 'richtig-1234' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
}

it('sagt, warum es nicht weitergeht, wenn der Browser das Sitzungs-Cookie verwirft', async () => {
	// über http:// verwirft der Browser das Cookie mit „Secure“ still, /me antwortet dann 401
	api.login.mockResolvedValueOnce({ user });
	api.me.mockRejectedValueOnce(new ApiError(401, 'Nicht angemeldet'));
	await signIn();
	expect(await screen.findByText(/Sitzungs-Cookie verworfen/)).toBeInTheDocument();
	expect(screen.getByText(/HTTPS/)).toBeInTheDocument();
	expect(goto).not.toHaveBeenCalled();
});

it('geht nach geprüfter Sitzung zur gewünschten Seite', async () => {
	goto.mockClear();
	api.login.mockResolvedValueOnce({ user });
	api.me.mockResolvedValueOnce({ user });
	await signIn();
	await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/', expect.anything()));
	expect(api.me).toHaveBeenLastCalledWith({ redirectOn401: false });
});

it('nennt den Grund, wenn die eigene Rolle geändert wurde', () => {
	state.url = new URL('https://ems.lan/login?grund=rolle');
	render(Page);
	expect(screen.getByText('Deine Rolle wurde geändert – bitte neu anmelden.')).toBeInTheDocument();
	expect(screen.queryByText(/Sitzung abgelaufen/)).not.toBeInTheDocument();
});
