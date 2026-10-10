import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Page from './+page.svelte';

const { api, goto, live } = vi.hoisted(() => ({
	api: {
		users: vi.fn(),
		updateUser: vi.fn(),
		createUser: vi.fn(),
		deleteUser: vi.fn()
	},
	goto: vi.fn(),
	live: { stop: vi.fn() }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$lib/live.svelte', () => ({ live }));
vi.mock('$app/state', () => ({
	page: { data: { user: { id: 1, username: 'admin', role: 'admin' } } }
}));

const user = (id: number, username: string, role: 'admin' | 'viewer') => ({
	id,
	username,
	role,
	totp_enabled: false,
	created_at: '2026-10-09T08:00:00+00:00'
});

async function pickOwnRole() {
	api.users.mockResolvedValue([user(1, 'admin', 'admin'), user(2, 'gast', 'viewer')]);
	render(Page);
	const select = await screen.findByLabelText<HTMLSelectElement>('Rolle von admin');
	await fireEvent.change(select, { target: { value: 'viewer' } });
	return select;
}

it('Schließen mit × lässt die eigene Rolle unverändert', async () => {
	const select = await pickOwnRole();
	await fireEvent.click(screen.getByRole('button', { name: 'Schließen' }));
	expect(select.value).toBe('admin');
	expect(api.updateUser).not.toHaveBeenCalled();
});

it('nach bestätigtem Wechsel der eigenen Rolle geht es mit Grund zur Anmeldung', async () => {
	api.updateUser.mockResolvedValueOnce(user(1, 'admin', 'viewer'));
	await pickOwnRole();
	await fireEvent.click(screen.getByRole('button', { name: 'Rolle ändern' }));
	await vi.waitFor(() =>
		expect(goto).toHaveBeenCalledWith('/login?grund=rolle', expect.anything())
	);
	expect(api.updateUser).toHaveBeenCalledWith(1, { role: 'viewer' });
	expect(live.stop).toHaveBeenCalled();
});
