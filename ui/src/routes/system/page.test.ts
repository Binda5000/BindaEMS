import { render, screen } from '@testing-library/svelte';
import * as v from 'valibot';
import { expect, it, vi } from 'vitest';
import pricesJson from '$lib/api/contract/prices.json';
import { PricesResponseSchema } from '$lib/api/schemas';
import { system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		prices: vi.fn(),
		forecast: vi.fn(),
		health: vi.fn(),
		refreshPrices: vi.fn()
	}
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/state', () => ({
	page: { data: { user: { id: 1, username: 'admin', role: 'admin' } } }
}));

it('zeigt gleichlautende Hinweise und Preisfehler ohne abzustürzen', async () => {
	const warning = 'Tarifbestandteil „Netznutzungsentgelt“ hat noch keinen Wert.';
	const error = 'Zeitüberschreitung beim Abruf';
	const prices = v.parse(PricesResponseSchema, pricesJson);
	api.system.mockResolvedValue({ ...system(), warnings: [warning, warning] });
	api.prices.mockResolvedValue({ ...prices, status: { ...prices.status, errors: [error, error] } });
	api.forecast.mockReturnValue(new Promise(() => {}));
	api.health.mockReturnValue(new Promise(() => {}));
	render(Page);
	expect(await screen.findAllByText(warning)).toHaveLength(2);
	expect(await screen.findAllByText(error)).toHaveLength(2);
});
