import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from './api/errors';
import { Resource } from './resource.svelte';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('lädt zuerst im Vordergrund, danach im Takt im Hintergrund', async () => {
	const calls: boolean[] = [];
	const resource = new Resource(
		async ({ background }) => {
			calls.push(background);
			return calls.length;
		},
		{ intervalMs: 15_000, hidden: () => false }
	);
	const stop = resource.start();
	await vi.advanceTimersByTimeAsync(30_000);
	expect(calls).toEqual([false, true, true]);
	expect(resource.data).toBe(3);
	stop();
});

it('fragt in verborgenen Tabs nicht ab und holt beim Zurückkehren nach', async () => {
	let hidden = false;
	const calls: boolean[] = [];
	const resource = new Resource(
		async ({ background }) => {
			calls.push(background);
			return 1;
		},
		{ intervalMs: 15_000, hidden: () => hidden }
	);
	const stop = resource.start();
	await vi.advanceTimersByTimeAsync(0);
	hidden = true;
	await vi.advanceTimersByTimeAsync(60_000);
	expect(calls).toEqual([false]);
	hidden = false;
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(0);
	expect(calls).toEqual([false, true]);
	stop();
});

it('behält die letzten Daten bei einem Fehler', async () => {
	const load = vi
		.fn()
		.mockResolvedValueOnce('alt')
		.mockRejectedValueOnce(new ApiError(502, 'InfluxDB nicht erreichbar'))
		.mockResolvedValueOnce('neu');
	const resource = new Resource(load, { intervalMs: 1000, hidden: () => false });
	const stop = resource.start();
	await vi.advanceTimersByTimeAsync(1000);
	expect([resource.data, resource.error?.detail]).toEqual(['alt', 'InfluxDB nicht erreichbar']);
	await vi.advanceTimersByTimeAsync(1000);
	expect([resource.data, resource.error]).toEqual(['neu', null]);
	stop();
});

it('stop bricht die laufende Anfrage ab', async () => {
	let signal: AbortSignal | undefined;
	const resource = new Resource(({ signal: s }) => {
		signal = s;
		return new Promise(() => {});
	});
	const stop = resource.start();
	stop();
	expect(signal?.aborted).toBe(true);
});
