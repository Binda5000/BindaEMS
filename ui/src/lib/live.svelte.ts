// Live-Verbindung zum Server (WebSocket /api/live): Zustand des core, Alarme, Neuverbinden
import * as v from 'valibot';
import { LiveMessageSchema, type Alarm, type CoreState } from './api/schemas';

export type LiveStatus =
	'connecting' | 'open' | 'reconnecting' | 'unauthorized' | 'forbidden' | 'stopped';

export const STALE_AFTER_MS = 5000;
export const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000] as const; // danach immer 30 s
const WS_UNAUTHORIZED = 4401; // Sitzung fehlt oder ist abgelaufen
const WS_FORBIDDEN = 4403; // fremde Herkunft
const CLOCK_MS = 1000;

export interface SocketLike {
	onopen: ((event: unknown) => void) | null;
	onmessage: ((event: { data: unknown }) => void) | null;
	onclose: ((event: { code: number }) => void) | null;
	close(): void;
}

export interface LiveOptions {
	url?: string;
	connect?: (url: string) => SocketLike;
	onUnauthorized?: () => void;
}

export function liveUrl(location: { protocol: string; host: string }): string {
	return `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/live`;
}

function openWebSocket(url: string): SocketLike {
	// Die Ereignisse des Browsers tragen `data` und `code` wie SocketLike
	return new WebSocket(url) as unknown as SocketLike;
}

export class LiveConnection {
	status = $state<LiveStatus>('stopped');
	coreConnected = $state(false);
	state = $state.raw<CoreState | null>(null);
	alarms = $state.raw<Alarm[]>([]);
	lastMessageAt = $state<number | null>(null);
	/** Wird bei abgelaufener Sitzung (4401) aufgerufen; die Seite trägt ihn nach dem Laden ein. */
	onUnauthorized: (() => void) | null;

	#now = $state(Date.now()); // schreitet jede Sekunde fort, damit `stale` kippen kann
	readonly stale = $derived(
		this.status !== 'open' ||
			!this.coreConnected ||
			this.lastMessageAt === null ||
			this.#now - this.lastMessageAt > STALE_AFTER_MS
	);

	#url: string | undefined;
	#connect: (url: string) => SocketLike;
	#socket: SocketLike | null = null;
	#attempt = 0;
	#retry: ReturnType<typeof setTimeout> | null = null;
	#clock: ReturnType<typeof setInterval> | null = null;

	constructor(options: LiveOptions = {}) {
		this.#url = options.url;
		this.#connect = options.connect ?? openWebSocket;
		this.onUnauthorized = options.onUnauthorized ?? null;
	}

	start(): void {
		if (this.#socket !== null || this.#retry !== null) return; // läuft schon
		this.#attempt = 0;
		this.#clock = setInterval(() => (this.#now = Date.now()), CLOCK_MS);
		this.#open();
	}

	stop(): void {
		this.#halt();
		this.status = 'stopped';
	}

	#open(): void {
		this.status = this.#attempt === 0 ? 'connecting' : 'reconnecting';
		const socket = this.#connect(this.#url ?? liveUrl(location));
		this.#socket = socket;
		socket.onopen = () => {
			if (this.#socket === socket) this.status = 'open';
		};
		socket.onmessage = (event) => {
			if (this.#socket === socket) this.#receive(event.data);
		};
		socket.onclose = (event) => {
			if (this.#socket === socket) this.#closed(event.code);
		};
	}

	#receive(raw: unknown): void {
		let data: unknown;
		try {
			data = JSON.parse(String(raw));
		} catch {
			return; // kaputte Nachricht: übergehen
		}
		const result = v.safeParse(LiveMessageSchema, data);
		if (!result.success) return;
		const message = result.output;
		this.lastMessageAt = this.#now = Date.now();
		switch (message.type) {
			case 'hello':
				this.state = message.data.state;
				this.alarms = message.data.alarms;
				this.coreConnected = message.data.core_connected;
				this.#attempt = 0; // Verbindung gelungen: Wartezeit beginnt wieder bei 1 s
				break;
			case 'state':
				this.state = message.data;
				break;
			case 'alarm':
				this.alarms = message.data;
				break;
			case 'core':
				this.coreConnected = message.data.connected;
				break;
		}
	}

	#closed(code: number): void {
		this.#socket = null;
		if (code === WS_UNAUTHORIZED) {
			this.#halt();
			this.status = 'unauthorized';
			this.onUnauthorized?.();
			return;
		}
		if (code === WS_FORBIDDEN) {
			this.#halt();
			this.status = 'forbidden';
			return;
		}
		this.status = 'reconnecting';
		const delay = RECONNECT_DELAYS_MS[Math.min(this.#attempt, RECONNECT_DELAYS_MS.length - 1)];
		this.#attempt += 1;
		this.#retry = setTimeout(() => {
			this.#retry = null;
			this.#open();
		}, delay);
	}

	/** Schließt die Verbindung und räumt alle Zeitgeber ab. */
	#halt(): void {
		const socket = this.#socket;
		this.#socket = null;
		if (socket !== null) {
			socket.onopen = socket.onmessage = socket.onclose = null;
			socket.close();
		}
		if (this.#retry !== null) clearTimeout(this.#retry);
		if (this.#clock !== null) clearInterval(this.#clock);
		this.#retry = this.#clock = null;
	}
}

/** Eine Verbindung für die ganze App; das Layout startet sie nach der Anmeldung. */
export const live = new LiveConnection();
