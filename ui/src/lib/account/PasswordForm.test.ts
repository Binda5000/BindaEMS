import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import PasswordForm from './PasswordForm.svelte';

async function fill(old: string, next: string, repeat: string) {
	await fireEvent.input(screen.getByLabelText('Altes Passwort'), { target: { value: old } });
	await fireEvent.input(screen.getByLabelText('Neues Passwort'), { target: { value: next } });
	await fireEvent.input(screen.getByLabelText('Neues Passwort wiederholen'), {
		target: { value: repeat }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));
}

it('prüft Länge und Wiederholung vor dem Senden', async () => {
	const change = vi.fn();
	render(PasswordForm, { change });
	await fill('alt-passwort-1', 'kurz', 'kurz');
	expect(screen.getByText('Mindestens 10 Zeichen')).toBeInTheDocument();
	await fill('alt-passwort-1', 'neues-passwort-1', 'neues-passwort-2');
	expect(screen.getByText('Passwörter stimmen nicht überein')).toBeInTheDocument();
	expect(change).not.toHaveBeenCalled();
});

it('meldet ein falsches altes Passwort und bestätigt die Änderung', async () => {
	const change = vi
		.fn()
		.mockRejectedValueOnce(new ApiError(400, 'Altes Passwort falsch'))
		.mockResolvedValueOnce(undefined);
	render(PasswordForm, { change });
	await fill('falsch-falsch', 'neues-passwort-1', 'neues-passwort-1');
	expect(await screen.findByText('Altes Passwort falsch')).toBeInTheDocument();
	await fill('alt-passwort-1', 'neues-passwort-1', 'neues-passwort-1');
	expect(
		await screen.findByText('Passwort geändert. Andere Sitzungen wurden abgemeldet.')
	).toBeInTheDocument();
});
