import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { limits, settingsCurrent } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: {
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
vi.mock('$app/state', () => ({
	page: { data: { user: { id: 1, username: 'admin', role: 'admin' } } }
}));

it('zeigt gleichlautende Hinweise ohne abzustürzen', async () => {
	// zwei Bestandteile gleichen Namens (verschiedene Gültigkeit), beide noch ohne Wert
	const warning = 'Tarifbestandteil „Netznutzungsentgelt“ hat noch keinen Wert.';
	api.settings.mockResolvedValue({ ...settingsCurrent(), warnings: [warning, warning] });
	api.limits.mockResolvedValue(limits());
	api.settingsVersions.mockReturnValue(new Promise(() => {}));
	render(Page);
	expect(await screen.findAllByText(warning)).toHaveLength(2);
});
