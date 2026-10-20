import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { limits, sensorCandidates, settingsCurrent } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, user } = vi.hoisted(() => ({
	user: { id: 1, username: 'admin', role: 'admin' },
	api: {
		learningCandidates: vi.fn(),
		settings: vi.fn(),
		limits: vi.fn(),
		settingsVersions: vi.fn(),
		saveSettings: vi.fn(),
		reprice: vi.fn(),
		importSettings: vi.fn()
	}
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/state', () => ({ page: { data: { user } } }));

it('zeigt gleichlautende Hinweise ohne abzustürzen', async () => {
	// zwei Bestandteile gleichen Namens (verschiedene Gültigkeit), beide noch ohne Wert
	const warning = 'Tarifbestandteil „Netznutzungsentgelt“ hat noch keinen Wert.';
	api.settings.mockResolvedValue({ ...settingsCurrent(), warnings: [warning, warning] });
	api.limits.mockResolvedValue(limits());
	api.settingsVersions.mockReturnValue(new Promise(() => {}));
	render(Page);
	expect(await screen.findAllByText(warning)).toHaveLength(2);
});

it('zeigt den Abschnitt Lastmodell; ein Viewer sieht ihn nur lesbar, ohne Abfrage der Kandidaten', async () => {
	user.role = 'viewer';
	api.settings.mockResolvedValue(settingsCurrent());
	api.limits.mockResolvedValue(limits());
	render(Page);
	await screen.findByText('Lastmodell verwenden');
	expect(screen.getByText('sensor.aussentemperatur')).toBeInTheDocument();
	expect(screen.queryByRole('button', { name: 'Bearbeiten' })).toBeNull();
	expect(api.learningCandidates).not.toHaveBeenCalled();
	user.role = 'admin';
});

it('lädt die Kandidaten erst, wenn der Admin den Editor öffnet', async () => {
	api.learningCandidates.mockClear();
	api.settings.mockResolvedValue(settingsCurrent());
	api.limits.mockResolvedValue(limits());
	api.settingsVersions.mockReturnValue(new Promise(() => {}));
	api.learningCandidates.mockResolvedValue(sensorCandidates());
	render(Page);
	await fireEvent.click(await screen.findByRole('button', { name: 'Bearbeiten' }));
	expect(
		await screen.findByRole('checkbox', { name: 'binary_sensor.anwesenheit' })
	).toBeInTheDocument();
	expect(api.learningCandidates).toHaveBeenCalledTimes(1);
});
