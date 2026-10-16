import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';

const { acknowledge } = vi.hoisted(() => ({ acknowledge: vi.fn() })); // vi.mock wird gehoben
vi.mock('$lib/api/endpoints', () => ({ api: { acknowledge } }));
const { Acknowledgement } = await import('./acknowledge.svelte');

it('zeigt die Antwort des core und lädt Health neu', async () => {
	acknowledge.mockResolvedValueOnce({ detail: 'Quittiert.' });
	const after = vi.fn(async () => {});
	const ack = new Acknowledgement(after);
	await ack.run('wallbox.evcs');
	expect(acknowledge).toHaveBeenCalledWith('wallbox.evcs');
	expect(after).toHaveBeenCalledOnce();
	expect(ack.notice).toEqual({ level: 'info', text: 'Quittiert.' });
	expect(ack.busy).toBe(false);
});

it('gibt „Quittieren“ wieder frei, auch wenn das Neuladen scheitert', async () => {
	acknowledge.mockRejectedValueOnce(new ApiError(409, 'Ursache besteht weiter: DESS ist aktiv.'));
	const ack = new Acknowledgement(async () => {
		throw new ApiError(0, 'Keine Verbindung');
	});
	await expect(ack.run('wallbox.evcs')).rejects.toThrow();
	expect(ack.busy).toBe(false);
	expect(ack.notice).toEqual({ level: 'warning', text: 'Ursache besteht weiter: DESS ist aktiv.' });
});
