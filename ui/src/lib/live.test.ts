import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import hello from './api/contract/live-hello.json';
import stateMessage from './api/contract/live-state.json';
import { LiveConnection, RECONNECT_DELAYS_MS, STALE_AFTER_MS, liveUrl } from './live.svelte';

class FakeSocket {
	onopen: ((event: unknown) => void) | null = null;
	onmessage: ((event: { data: unknown }) => void) | null = null;
	onclose: ((event: { code: number }) => void) | null = null;
	close() {}
	open() {
		this.onopen?.({});
	}
	send(message: unknown) {
		this.onmessage?.({ data: JSON.stringify(message) });
	}
	drop(code = 1006) {
		this.onclose?.({ code });
	}
}

let sockets: FakeSocket[] = [];

function connect(onUnauthorized = vi.fn()) {
	sockets = [];
	const live = new LiveConnection({
		url: 'ws://test/api/live',
		connect: () => {
			const socket = new FakeSocket();
			sockets.push(socket);
			return socket;
		},
		onUnauthorized
	});
	live.start();
	return live;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('übernimmt hello, state und alarm', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	expect([live.status, live.coreConnected]).toEqual(['open', true]);
	sockets[0].send(stateMessage);
	expect(live.state?.derived.pv_total_w).toBe(stateMessage.data.derived.pv_total_w);
	const alarm = {
		id: 'competitor.dess',
		severity: 'warning',
		message: 'DESS aktiv',
		since: '2026-10-09T08:00:00+00:00'
	};
	sockets[0].send({ type: 'alarm', data: [alarm] });
	expect(live.alarms.map((a) => a.id)).toEqual(['competitor.dess']);
});

it('markiert Daten nach 5 s ohne Nachricht als veraltet', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	expect(live.stale).toBe(false);
	vi.advanceTimersByTime(STALE_AFTER_MS + 1000);
	expect(live.stale).toBe(true);
	sockets[0].send(stateMessage);
	expect(live.stale).toBe(false);
});

it('meldet einen getrennten core als veraltet', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	sockets[0].send({ type: 'core', data: { connected: false } });
	expect([live.coreConnected, live.stale]).toEqual([false, true]);
});

it('verbindet mit 1, 2, 5, 10, 30 s Abstand neu', () => {
	connect();
	for (const delay of [...RECONNECT_DELAYS_MS, 30_000]) {
		sockets.at(-1)?.drop();
		const before = sockets.length;
		vi.advanceTimersByTime(delay - 1);
		expect(sockets.length).toBe(before);
		vi.advanceTimersByTime(1);
		expect(sockets.length).toBe(before + 1);
	}
});

it('beginnt nach einer gelungenen Verbindung wieder mit 1 s', () => {
	connect();
	sockets[0].drop();
	vi.advanceTimersByTime(1000);
	sockets[1].drop();
	vi.advanceTimersByTime(2000);
	sockets[2].open();
	sockets[2].send(hello);
	sockets[2].drop();
	vi.advanceTimersByTime(1000);
	expect(sockets).toHaveLength(4);
});

it('4401 meldet die abgelaufene Sitzung und verbindet nicht neu', () => {
	const onUnauthorized = vi.fn();
	const live = connect(onUnauthorized);
	sockets[0].drop(4401);
	vi.advanceTimersByTime(60_000);
	expect(live.status).toBe('unauthorized');
	expect(onUnauthorized).toHaveBeenCalledOnce();
	expect(sockets).toHaveLength(1);
});

it('4403 bleibt getrennt', () => {
	const live = connect();
	sockets[0].drop(4403);
	vi.advanceTimersByTime(60_000);
	expect([live.status, sockets.length]).toEqual(['forbidden', 1]);
});

it('übergeht kaputte Nachrichten', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	sockets[0].onmessage?.({ data: 'kein JSON' });
	sockets[0].send({ type: 'unbekannt', data: 1 });
	expect(live.state).toEqual(hello.data.state);
});

it('baut die Adresse aus dem Ursprung der Seite', () => {
	expect(liveUrl({ protocol: 'https:', host: 'ems.lan' })).toBe('wss://ems.lan/api/live');
	expect(liveUrl({ protocol: 'http:', host: '127.0.0.1:8099' })).toBe(
		'ws://127.0.0.1:8099/api/live'
	);
});
