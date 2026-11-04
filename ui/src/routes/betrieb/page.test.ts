import { render, screen, within } from '@testing-library/svelte';
import * as v from 'valibot';
import { beforeEach, expect, it, vi } from 'vitest';
import opsCheckJson from '$lib/api/contract/ops-check.json';
import opsFindingsJson from '$lib/api/contract/ops-findings.json';
import opsSwitchesJson from '$lib/api/contract/ops-switches.json';
import { ApiError } from '$lib/api/errors';
import { OpsCheckSchema, OpsFindingsSchema, OpsSwitchesSchema } from '$lib/api/schemas';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: { opsCheck: vi.fn(), opsFindings: vi.fn(), opsSwitches: vi.fn() }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));

const check = () => v.parse(OpsCheckSchema, opsCheckJson);
const findings = () => v.parse(OpsFindingsSchema, opsFindingsJson);
const switches = () => v.parse(OpsSwitchesSchema, opsSwitchesJson);

beforeEach(() => {
	for (const mock of Object.values(api)) mock.mockReset();
	api.opsCheck.mockResolvedValue(check());
	api.opsFindings.mockResolvedValue(findings());
	api.opsSwitches.mockResolvedValue(switches());
});

function card(name: string): HTMLElement {
	return screen.getByRole('heading', { level: 2, name }).closest('section') as HTMLElement;
}

it('zeigt Betriebscheck, Auffälligkeiten der letzten 14 Tage und Schalter', async () => {
	render(Page);
	const first = check().items.find((item) => item.level !== 'ok');
	expect(await screen.findByText(first!.text)).toBeInTheDocument();
	const report = findings().report;
	expect(await within(card('Auffälligkeiten')).findByText(report!.text)).toBeInTheDocument();
	const changed = switches().switches.find((item) => item.changed);
	expect(await within(card('Schalter')).findByText(changed!.label)).toBeInTheDocument();
	expect(api.opsFindings).toHaveBeenCalledWith(14, expect.anything());
	expect(api.opsCheck).toHaveBeenCalledTimes(1);
	expect(api.opsSwitches).toHaveBeenCalledTimes(1);
});

it('zeigt einen Fehler je Karte, die anderen Karten bleiben', async () => {
	api.opsFindings.mockRejectedValue(new ApiError(500, 'Auffälligkeiten nicht lesbar'));
	render(Page);
	expect(
		await within(card('Auffälligkeiten')).findByText('Auffälligkeiten nicht lesbar')
	).toBeInTheDocument();
	const changed = switches().switches.find((item) => item.changed);
	expect(await within(card('Schalter')).findByText(changed!.label)).toBeInTheDocument();
});

it('fragt den Betriebscheck alle 15 s, Auffälligkeiten und Schalter alle 5 min', async () => {
	vi.useFakeTimers();
	try {
		render(Page);
		await vi.advanceTimersByTimeAsync(15_000);
		expect(api.opsCheck).toHaveBeenCalledTimes(2);
		expect(api.opsFindings).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(300_000);
		expect(api.opsFindings).toHaveBeenCalledTimes(2);
		expect(api.opsSwitches).toHaveBeenCalledTimes(2);
	} finally {
		vi.useRealTimers();
	}
});
