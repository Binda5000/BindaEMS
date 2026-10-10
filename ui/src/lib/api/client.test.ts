import * as v from 'valibot';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { onUnauthorized, request } from './client';
import { ApiError, CONTRACT_ERROR, NETWORK_ERROR } from './errors';

const fetchMock = vi.fn();

function reply(status: number, body?: unknown, headers: Record<string, string> = {}) {
	fetchMock.mockResolvedValueOnce(
		new Response(body === undefined ? null : JSON.stringify(body), {
			status,
			headers: { 'Content-Type': 'application/json', ...headers }
		})
	);
}

function sentHeaders(call = 0): Headers {
	return new Headers(fetchMock.mock.calls[call][1].headers);
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	document.cookie = 'bindaems_csrf=tok-123';
});

afterEach(() => {
	vi.unstubAllGlobals();
	fetchMock.mockReset();
	onUnauthorized(null);
});

it('sendet CSRF-Token und JSON bei schreibenden Anfragen', async () => {
	reply(204);
	await expect(request('PUT', '/api/settings', { body: { a: 1 } })).resolves.toBeUndefined();
	const [url, init] = fetchMock.mock.calls[0];
	expect([url, init.method, init.body]).toEqual(['/api/settings', 'PUT', '{"a":1}']);
	expect(sentHeaders().get('X-CSRF-Token')).toBe('tok-123');
	expect(sentHeaders().get('Content-Type')).toBe('application/json');
});

it('kennzeichnet automatische Abfragen und liest ohne CSRF-Token', async () => {
	reply(200, { ok: true });
	await request('GET', '/api/system', { background: true });
	expect(sentHeaders().get('X-Bindaems-Background')).toBe('1');
	expect(sentHeaders().get('X-CSRF-Token')).toBeNull();
});

it('meldet die Sperre mit Wartezeit', async () => {
	reply(
		429,
		{ detail: 'Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr' },
		{
			'Retry-After': '900'
		}
	);
	const error = await request('POST', '/api/auth/password', { body: {} }).catch((e) => e);
	expect(error).toBeInstanceOf(ApiError);
	expect([error.status, error.detail, error.retryAfterS]).toEqual([
		429,
		'Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr',
		900
	]);
});

it('ruft bei 401 die Anmeldung auf, außer es ist abgeschaltet', async () => {
	const handler = vi.fn();
	onUnauthorized(handler);
	reply(401, { detail: 'Nicht angemeldet' });
	await request('GET', '/api/system').catch(() => undefined);
	reply(401, { detail: 'Benutzername oder Passwort falsch' });
	await request('POST', '/api/auth/login', { body: {}, redirectOn401: false }).catch(
		() => undefined
	);
	expect(handler).toHaveBeenCalledTimes(1);
});

it('weist Antworten zurück, die nicht zum Schema passen', async () => {
	reply(200, { user: { id: 'x' } });
	const schema = v.object({ user: v.object({ id: v.number() }) });
	const error = await request('GET', '/api/auth/me', { schema }).catch((e) => e);
	expect(error.detail).toBe(CONTRACT_ERROR);
});

it('meldet eine fehlende Verbindung', async () => {
	fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
	const error = await request('GET', '/api/system').catch((e) => e);
	expect([error.status, error.detail]).toEqual([0, NETWORK_ERROR]);
});

it('nennt Fehlerseiten des Proxys statt „Unbekannter Fehler“', async () => {
	// beim Neustart der app antwortet nginx mit einer HTML-Seite
	for (const status of [502, 504, 404]) {
		fetchMock.mockResolvedValueOnce(
			new Response('<html><body>Bad Gateway</body></html>', {
				status,
				headers: { 'Content-Type': 'text/html' }
			})
		);
	}
	await expect(request('GET', '/api/system')).rejects.toMatchObject({
		detail: 'Server nicht erreichbar (HTTP 502)'
	});
	await expect(request('GET', '/api/system')).rejects.toMatchObject({
		detail: 'Server nicht erreichbar (HTTP 504)'
	});
	await expect(request('GET', '/api/system')).rejects.toMatchObject({
		detail: 'Unbekannter Fehler (HTTP 404)'
	});
});
