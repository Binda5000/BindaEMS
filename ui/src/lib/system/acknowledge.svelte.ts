// Quittierung einer Sperre (Admin): die Antwort des core steht als Hinweis unter der Tabelle
import { api } from '$lib/api/endpoints';
import { ApiError } from '$lib/api/errors';

export interface AckNotice {
	level: 'info' | 'warning' | 'error';
	text: string;
}

export class Acknowledgement {
	busy = $state(false);
	notice = $state<AckNotice | null>(null);

	#after: () => Promise<void>;

	/** `after` lädt Health neu, damit die Sperre verschwindet, sobald der core sie löst. */
	constructor(after: () => Promise<void>) {
		this.#after = after;
	}

	/** 409 (Ursache besteht weiter, nicht gesperrt) ist eine Warnung, jeder andere Fehler ein Fehler. */
	async run(actuator: string): Promise<void> {
		this.busy = true;
		this.notice = null;
		try {
			try {
				this.notice = { level: 'info', text: (await api.acknowledge(actuator)).detail };
			} catch (error) {
				this.notice = {
					level: error instanceof ApiError && error.status === 409 ? 'warning' : 'error',
					text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
				};
			}
			await this.#after();
		} finally {
			this.busy = false; // auch wenn das Neuladen scheitert: sonst bliebe „Quittieren“ gesperrt
		}
	}
}
