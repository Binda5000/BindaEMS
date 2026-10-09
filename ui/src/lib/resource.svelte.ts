// Daten einer Seite, die im Takt nachgeladen werden. Abfragen im Takt laufen „im Hintergrund“
// (verlängern die Sitzung nicht) und pausieren in verborgenen Tabs.
import { ApiError } from './api/errors';

export interface ResourceOptions {
	/** Takt in ms; ohne Angabe wird nur einmal geladen */
	intervalMs?: number;
	hidden?: () => boolean;
}

type Loader<T> = (options: { background: boolean; signal: AbortSignal }) => Promise<T>;

function isAbort(error: unknown): boolean {
	return (error as { name?: unknown } | null)?.name === 'AbortError';
}

export class Resource<T> {
	/** letzte gelungene Ladung – bleibt bei Fehlern stehen */
	data = $state.raw<T | undefined>(undefined);
	error = $state.raw<ApiError | null>(null);
	loading = $state(false);

	#load: Loader<T>;
	#intervalMs: number | undefined;
	#hidden: () => boolean;
	#running = false;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#controller: AbortController | null = null;

	constructor(load: Loader<T>, options: ResourceOptions = {}) {
		this.#load = load;
		this.#intervalMs = options.intervalMs;
		this.#hidden = options.hidden ?? (() => document.visibilityState === 'hidden');
	}

	/** Erste Ladung im Vordergrund, danach im Takt im Hintergrund; gibt `stop` zurück. */
	start(): () => void {
		if (!this.#running) {
			this.#running = true;
			document.addEventListener('visibilitychange', this.#onVisibility);
			void this.#run(false);
		}
		return () => this.#stop();
	}

	/** Neu laden im Vordergrund (nach einer Bedienung). */
	refresh(): Promise<void> {
		return this.#run(false);
	}

	#onVisibility = (): void => {
		if (this.#running && !this.#hidden()) void this.#run(true);
	};

	async #run(background: boolean): Promise<void> {
		this.#clearTimer();
		this.#controller?.abort();
		const controller = new AbortController();
		this.#controller = controller;
		this.loading = true;
		try {
			const data = await this.#load({ background, signal: controller.signal });
			if (controller.signal.aborted) return;
			this.data = data;
			this.error = null;
		} catch (error) {
			if (controller.signal.aborted || isAbort(error)) return;
			this.error =
				error instanceof ApiError
					? error
					: new ApiError(0, error instanceof Error ? error.message : String(error));
		} finally {
			if (this.#controller === controller) {
				this.#controller = null;
				this.loading = false;
				this.#schedule();
			}
		}
	}

	#schedule(): void {
		if (!this.#running || this.#intervalMs === undefined) return;
		this.#timer = setTimeout(() => {
			this.#timer = null;
			// verborgen: nur weiterzählen; beim Zurückkehren lädt `visibilitychange`
			if (this.#hidden()) this.#schedule();
			else void this.#run(true);
		}, this.#intervalMs);
	}

	#clearTimer(): void {
		if (this.#timer !== null) clearTimeout(this.#timer);
		this.#timer = null;
	}

	#stop(): void {
		this.#running = false;
		document.removeEventListener('visibilitychange', this.#onVisibility);
		this.#clearTimer();
		this.#controller?.abort();
		this.#controller = null;
		this.loading = false;
	}
}
