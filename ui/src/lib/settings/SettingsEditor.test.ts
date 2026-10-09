import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import error422 from '$lib/api/contract/error-422.json';
import { settingsCurrent } from '$lib/testing/fixtures';
import SettingsEditor from './SettingsEditor.svelte';

const LOSS = 'Netzverlustentgelt (ct/kWh netto)';

it('Konflikt behält die Eingaben und bietet Neuladen an', async () => {
	const save = vi
		.fn()
		.mockRejectedValueOnce(
			new ApiError(409, 'Die Einstellungen wurden inzwischen geändert (aktuell Version 7).')
		);
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(screen.getByLabelText(LOSS), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(
		await screen.findByText('Die Einstellungen wurden inzwischen geändert (aktuell Version 7).')
	).toBeInTheDocument();
	expect(screen.getByLabelText(LOSS)).toHaveValue('1,23');
	expect(
		screen.getByRole('button', { name: 'Neu laden (Eingaben verwerfen)' })
	).toBeInTheDocument();
});

it('Serverfehler erscheinen am Feld', async () => {
	const save = vi.fn().mockRejectedValueOnce(new ApiError(422, 'x', { detail: error422.detail }));
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(screen.getByLabelText(LOSS), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('Zahl erwartet')).toBeInTheDocument();
});

it('meldet nach dem Speichern, ob sich Tarifwerte geändert haben', async () => {
	const current = settingsCurrent();
	const saved = { ...current, version: current.version + 1 };
	const onSaved = vi.fn();
	render(SettingsEditor, {
		current,
		save: vi.fn().mockResolvedValueOnce(saved),
		onSaved,
		onReload: vi.fn()
	});
	await fireEvent.input(screen.getByLabelText(LOSS), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true));
});
