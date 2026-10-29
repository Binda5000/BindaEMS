import { render, screen } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import catalogJson from '$lib/api/contract/history-catalog.json';
import { limits } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: { historyCatalog: vi.fn(), history: vi.fn(), ledgerDays: vi.fn(), limits: vi.fn() }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/state', () => ({ page: { url: new URL('https://ems.lan/verlauf') } }));

afterEach(() => {
	vi.useRealTimers();
});

it('„Heute“ rückt um Mitternacht auf den neuen Tag weiter', async () => {
	vi.useFakeTimers({
		toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout']
	});
	vi.setSystemTime(new Date('2026-10-09T21:59:00Z')); // 23:59 in Wien
	api.historyCatalog.mockReturnValue(new Promise(() => {}));
	api.history.mockReturnValue(new Promise(() => {}));
	api.ledgerDays.mockReturnValue(new Promise(() => {}));
	api.limits.mockReturnValue(new Promise(() => {}));
	render(Page);
	expect(screen.getByLabelText<HTMLInputElement>('Erster Tag').value).toBe('2026-10-09');

	await vi.advanceTimersByTimeAsync(2 * 60_000); // 00:01 am 10.10.
	expect(screen.getByLabelText<HTMLInputElement>('Erster Tag').value).toBe('2026-10-10');
	expect(screen.getByLabelText<HTMLInputElement>('Letzter Tag').value).toBe('2026-10-10');
	expect(api.ledgerDays).toHaveBeenLastCalledWith('2026-10-10', '2026-10-10', expect.anything());
	expect(api.history.mock.lastCall?.[1]).toEqual({
		from: '2026-10-09T22:00:00.000Z',
		to: '2026-10-10T22:00:00.000Z'
	});
});

it('nennt die Wallbox-Reihen beim Namen aus den Einstellungen', async () => {
	api.historyCatalog.mockResolvedValue(catalogJson);
	api.history.mockReturnValue(new Promise(() => {}));
	api.ledgerDays.mockReturnValue(new Promise(() => {}));
	api.limits.mockResolvedValue(limits());
	render(Page);
	expect(await screen.findByRole('checkbox', { name: /^EVCS/ })).toBeInTheDocument();
	expect(screen.getByRole('checkbox', { name: /^Garage/ })).toBeInTheDocument();
	expect(screen.queryByRole('checkbox', { name: /Wallbox evcs/ })).not.toBeInTheDocument();
});
