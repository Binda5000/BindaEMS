// HTTP-Zugriff auf die API: Sitzungs-Cookie, CSRF-Token, Kennzeichen für automatische Abfragen,
// deutsche Fehlertexte und Prüfung der Antworten gegen ihr Schema
import * as v from 'valibot';
import { ApiError, CONTRACT_ERROR, NETWORK_ERROR, detailText } from './errors';

/** Automatische Abfragen im Takt verlängern die Sitzung nicht (Wert „1“). */
export const BACKGROUND_HEADER = 'X-Bindaems-Background';
const CSRF_HEADER = 'X-CSRF-Token';
const CSRF_COOKIE = 'bindaems_csrf';

export type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface RequestOptions<T> {
	schema?: v.GenericSchema<unknown, T>;
	body?: unknown;
	background?: boolean;
	signal?: AbortSignal;
	/** `false`: ein 401 meldet nur den Fehler (Anmeldung selbst), statt zur Anmeldung zu führen */
	redirectOn401?: boolean;
}

let unauthorizedHandler: (() => void) | null = null;

/** Wird bei jedem 401 aufgerufen (Sitzung abgelaufen). */
export function onUnauthorized(handler: (() => void) | null): void {
	unauthorizedHandler = handler;
}

export function csrfToken(cookies: string = document.cookie): string | null {
	for (const part of cookies.split(';')) {
		const [name, ...value] = part.trim().split('=');
		if (name === CSRF_COOKIE) return decodeURIComponent(value.join('='));
	}
	return null;
}

function isAbort(error: unknown): boolean {
	return (error as { name?: unknown } | null)?.name === 'AbortError';
}

function retryAfter(value: string | null): number | null {
	const seconds = value === null || value.trim() === '' ? Number.NaN : Number(value);
	return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

async function readBody(response: Response): Promise<unknown> {
	const text = await response.text();
	if (!text) return undefined;
	try {
		return JSON.parse(text);
	} catch {
		return text;
	}
}

export async function request<T = void>(
	method: Method,
	path: string,
	options: RequestOptions<T> = {}
): Promise<T> {
	const headers: Record<string, string> = { Accept: 'application/json' };
	let body: string | undefined;
	if (options.body !== undefined) {
		headers['Content-Type'] = 'application/json';
		body = JSON.stringify(options.body);
	}
	if (method !== 'GET') {
		const token = csrfToken();
		if (token) headers[CSRF_HEADER] = token;
	}
	if (options.background) headers[BACKGROUND_HEADER] = '1';

	let response: Response;
	let data: unknown;
	try {
		response = await fetch(path, {
			method,
			headers,
			body,
			signal: options.signal,
			credentials: 'same-origin'
		});
		data = await readBody(response);
	} catch (error) {
		if (isAbort(error)) throw error;
		throw new ApiError(0, NETWORK_ERROR);
	}

	if (!response.ok) {
		if (response.status === 401 && options.redirectOn401 !== false) unauthorizedHandler?.();
		const detail =
			typeof data === 'object' && data !== null ? (data as { detail?: unknown }).detail : undefined;
		throw new ApiError(
			response.status,
			detailText(detail),
			data,
			retryAfter(response.headers.get('Retry-After'))
		);
	}
	if (!options.schema) return data as T;
	const result = v.safeParse(options.schema, data);
	if (!result.success) {
		console.error(
			`Unerwartete Antwort auf ${method} ${path}:`,
			result.issues.map((issue) => `${v.getDotPath(issue) ?? '(Wurzel)'}: ${issue.message}`)
		);
		throw new ApiError(response.status, CONTRACT_ERROR, data);
	}
	return result.output;
}
