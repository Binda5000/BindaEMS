import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import UserForm from './UserForm.svelte';

it('zeigt die Antwort des Servers bei einem doppelten Namen', async () => {
	const create = vi
		.fn()
		.mockRejectedValueOnce(new ApiError(409, 'Benutzer „max“ existiert bereits'));
	render(UserForm, { create, onCreated: vi.fn() });
	await fireEvent.input(screen.getByLabelText('Benutzername'), { target: { value: 'max' } });
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: 'lang-genug-1' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Anlegen' }));
	expect(await screen.findByText('Benutzer „max“ existiert bereits')).toBeInTheDocument();
});
