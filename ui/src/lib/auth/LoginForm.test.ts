import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import LoginForm from './LoginForm.svelte';

const user = {
	id: 2,
	username: 'sicher',
	role: 'admin',
	totp_enabled: true,
	created_at: '2026-10-09T08:00:00+00:00'
};
const totpRequired = new ApiError(401, 'Bestätigungscode erforderlich', {
	detail: 'Bestätigungscode erforderlich',
	totp_required: true
});

async function submit(username: string, password: string) {
	await fireEvent.input(screen.getByLabelText('Benutzername'), { target: { value: username } });
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: password } });
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
}

it('fragt nach dem Code, wenn TOTP aktiv ist', async () => {
	const login = vi.fn().mockRejectedValueOnce(totpRequired).mockResolvedValueOnce(user);
	const onSuccess = vi.fn();
	render(LoginForm, { login, onSuccess });
	await submit('sicher', 'demo-passwort-1');
	await fireEvent.input(await screen.findByLabelText('Bestätigungscode'), {
		target: { value: '123456' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
	expect(login).toHaveBeenLastCalledWith({
		username: 'sicher',
		password: 'demo-passwort-1',
		totp: '123456',
		remember: false
	});
	expect(onSuccess).toHaveBeenCalledWith(user);
});

it('behält das Codefeld nach einem falschen Code', async () => {
	const wrong = new ApiError(401, 'Benutzername oder Passwort falsch', {
		detail: 'Benutzername oder Passwort falsch'
	});
	const login = vi.fn().mockRejectedValueOnce(totpRequired).mockRejectedValueOnce(wrong);
	render(LoginForm, { login, onSuccess: vi.fn() });
	await submit('sicher', 'demo-passwort-1');
	await fireEvent.input(await screen.findByLabelText('Bestätigungscode'), {
		target: { value: '000000' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
	expect(await screen.findByText('Benutzername oder Passwort falsch')).toBeInTheDocument();
	expect(screen.getByLabelText('Bestätigungscode')).toBeInTheDocument();
});

it('zeigt die Sperre mit Uhrzeit', async () => {
	const locked = new ApiError(
		429,
		'Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr',
		{},
		900
	);
	render(LoginForm, { login: vi.fn().mockRejectedValueOnce(locked), onSuccess: vi.fn() });
	await submit('gast', 'falsch-falsch');
	expect(
		await screen.findByText('Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr')
	).toBeInTheDocument();
});
