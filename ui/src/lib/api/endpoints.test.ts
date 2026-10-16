import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from './endpoints';
import control from './contract/control-reply.json';
import decisions from './contract/decisions.json';
import plan from './contract/plan.json';
import gridCharge from './contract/plan-grid-charge.json';

const fetchMock = vi.fn();

function reply(body: unknown) {
	fetchMock.mockResolvedValueOnce(
		new Response(JSON.stringify(body), {
			status: 200,
			headers: { 'Content-Type': 'application/json' }
		})
	);
}

function sent(): { url: string; method: string; body: unknown; csrf: string | null } {
	const [url, init] = fetchMock.mock.calls[0];
	return {
		url,
		method: init.method,
		body: init.body === undefined ? undefined : JSON.parse(init.body),
		csrf: new Headers(init.headers).get('X-CSRF-Token')
	};
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	document.cookie = 'bindaems_csrf=tok-123';
});

afterEach(() => {
	vi.unstubAllGlobals();
	fetchMock.mockReset();
});

it('sendet Bedienungen als PUT mit CSRF-Token und prüft die Antwort', async () => {
	reply(control);
	const result = await api.setMode('tesla', 'now');
	expect(sent()).toEqual({
		url: '/api/control/vehicles/tesla/mode',
		method: 'PUT',
		body: { mode: 'now' },
		csrf: 'tok-123'
	});
	expect(result.intent.vehicles.tesla.mode).toBe('now');
});

it('sendet die Zuordnung „Automatisch“ als null', async () => {
	reply(control);
	await api.setWallboxVehicle('evcs', null);
	expect(sent()).toMatchObject({
		url: '/api/control/wallboxes/evcs/vehicle',
		body: { vehicle: null }
	});
});

it('sendet Passwort und Bestätigung der Live-Schaltung', async () => {
	reply(control);
	await api.setActuators({ 'wallbox.evcs': 'live' }, 'geheim', true);
	expect(sent()).toMatchObject({
		url: '/api/control/actuators',
		body: { modes: { 'wallbox.evcs': 'live' }, password: 'geheim', confirmed: true }
	});
});

it('quittiert mit POST und liest den Satz des core', async () => {
	reply({ detail: 'Quittiert.' });
	await expect(api.acknowledge('wallbox.evcs')).resolves.toEqual({ detail: 'Quittiert.' });
	expect(sent()).toMatchObject({ url: '/api/control/locks/wallbox.evcs/ack', method: 'POST' });
});

it('hängt nur gesetzte Filter an die Abfrage der Entscheidungen', async () => {
	reply(decisions);
	await api.decisions({
		actuator: 'vehicle.tesla',
		dry_run: false,
		before: 5,
		limit: 100,
		text: ''
	});
	const url = new URL(sent().url, 'http://x');
	expect(url.pathname).toBe('/api/decisions');
	expect(Object.fromEntries(url.searchParams)).toEqual({
		actuator: 'vehicle.tesla',
		dry_run: 'false',
		before: '5',
		limit: '100'
	});
});

it('liest den Plan mit GET', async () => {
	reply(plan);
	const result = await api.plan();
	expect(sent()).toMatchObject({ url: '/api/plan', method: 'GET' });
	expect(result.plan?.slots.length).toBeGreaterThan(0);
	expect(result.status.message).toBe('Plan von 10:00 Uhr');
});

it('fordert eine Neuplanung mit POST und CSRF-Token an', async () => {
	fetchMock.mockResolvedValueOnce(new Response('{"queued":true}', { status: 202 }));
	await expect(api.replan()).resolves.toBeUndefined();
	expect(sent()).toMatchObject({ url: '/api/plan/replan', method: 'POST', csrf: 'tok-123' });
});

it('schaltet das Netzladen mit PUT und prüft die Antwort', async () => {
	reply(gridCharge);
	const result = await api.setGridCharge(true);
	expect(sent()).toEqual({
		url: '/api/plan/grid-charge',
		method: 'PUT',
		body: { on: true },
		csrf: 'tok-123'
	});
	expect(result.grid_charge_allowed).toBe(true);
});
