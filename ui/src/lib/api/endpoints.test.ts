import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from './endpoints';
import boostCost from './contract/boost-cost.json';
import automation from './contract/control-automation.json';
import control from './contract/control-reply.json';
import decisions from './contract/decisions.json';
import plan from './contract/plan.json';
import review from './contract/plan-review.json';
import gridCharge from './contract/plan-grid-charge.json';
import sessions from './contract/charging-sessions.json';
import candidates from './contract/learning-candidates.json';
import learning from './contract/learning-load.json';
import driving from './contract/learning-driving.json';
import qualityDaily from './contract/forecast-quality-daily.json';
import checks from './contract/ledger-checks.json';
import statsMonth from './contract/stats-month.json';

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

it('sendet den Sofort-Höchststrom eines Fahrzeugs, „volle Leistung“ als null', async () => {
	reply(control);
	await api.setNowLimit('tesla', 10);
	expect(sent()).toEqual({
		url: '/api/control/vehicles/tesla/now-limit',
		method: 'PUT',
		body: { max_a: 10 },
		csrf: 'tok-123'
	});
	fetchMock.mockClear();
	reply(control);
	await api.setNowLimit('tesla', null);
	expect(sent().body).toEqual({ max_a: null });
});

it('sendet den Sofort-Höchststrom für Gäste, „volle Leistung“ als null', async () => {
	reply(control);
	await api.setGuestNowLimit(8);
	expect(sent()).toEqual({
		url: '/api/control/guest/now-limit',
		method: 'PUT',
		body: { max_a: 8 },
		csrf: 'tok-123'
	});
	fetchMock.mockClear();
	reply(control);
	await api.setGuestNowLimit(null);
	expect(sent().body).toEqual({ max_a: null });
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

it('sendet die Akku-Unterstützung als PUT, auch ohne eigene Schwelle', async () => {
	reply(control);
	await api.setSupport(false, null);
	expect(sent()).toEqual({
		url: '/api/control/support',
		method: 'PUT',
		body: { allowed: false, soc_pct: null },
		csrf: 'tok-123'
	});
});

it('sendet den Abfahrtstag nur, wenn er angegeben ist', async () => {
	reply(control);
	await api.setTarget('tesla', 50, '07:15', '2026-10-12');
	expect(sent()).toEqual({
		url: '/api/control/vehicles/tesla/target',
		method: 'PUT',
		body: { target_soc_pct: 50, departure: '07:15', departure_on: '2026-10-12' },
		csrf: 'tok-123'
	});
});

it('sendet Ziel-SOC und Abfahrt, ein unverändertes Feld als null', async () => {
	reply(control);
	await api.setTarget('tesla', 80, null);
	expect(sent()).toEqual({
		url: '/api/control/vehicles/tesla/target',
		method: 'PUT',
		body: { target_soc_pct: 80, departure: null },
		csrf: 'tok-123'
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

it('liest die Mehrkosten des Boosts mit GET', async () => {
	reply(boostCost);
	const result = await api.boostCost();
	expect(sent()).toMatchObject({ url: '/api/control/boost-cost', method: 'GET' });
	expect(result.applies).toBe(true);
	expect(result.vehicles.map((vehicle) => vehicle.id)).toEqual(['tesla', 'egolf']);
});

it('liest den Plan mit GET', async () => {
	reply(plan);
	const result = await api.plan();
	expect(sent()).toMatchObject({ url: '/api/plan', method: 'GET' });
	expect(result.plan?.slots.length).toBeGreaterThan(0);
	expect(result.status.message).toBe('Plan von 10:00 Uhr');
});

it('liest Plan gegen Ist mit GET', async () => {
	reply(review);
	const result = await api.planReview();
	expect(sent()).toMatchObject({ url: '/api/plan/review', method: 'GET' });
	expect(result.day).toBe('2026-10-09');
	expect(result.slots.filter((slot) => slot.planned && slot.actual)).toHaveLength(4);
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

it('liest die Statistik eines Zeitraums mit GET', async () => {
	reply(statsMonth);
	const result = await api.stats('month', '2026-10-09');
	const url = new URL(sent().url, 'http://x');
	expect(url.pathname).toBe('/api/stats');
	expect(Object.fromEntries(url.searchParams)).toEqual({ period: 'month', date: '2026-10-09' });
	expect(result.period).toBe('month');
	expect(result.vehicles.map((vehicle) => vehicle.key)).toContain('tesla');
});

it('liest die Statistik ohne Datum für heute', async () => {
	reply(statsMonth);
	await api.stats('year');
	const url = new URL(sent().url, 'http://x');
	expect(Object.fromEntries(url.searchParams)).toEqual({ period: 'year' });
});

it('liest die Ladesessions, das Auto nur wenn gesetzt', async () => {
	reply(sessions);
	const result = await api.chargingSessions('2026-09-25', '2026-10-09');
	let url = new URL(sent().url, 'http://x');
	expect(url.pathname).toBe('/api/charging/sessions');
	expect(Object.fromEntries(url.searchParams)).toEqual({ from: '2026-09-25', to: '2026-10-09' });
	expect(result.some((session) => session.open)).toBe(true);
	fetchMock.mockReset();
	reply(sessions);
	await api.chargingSessions('2026-09-25', '2026-10-09', 'guest');
	url = new URL(sent().url, 'http://x');
	expect(Object.fromEntries(url.searchParams)).toEqual({
		from: '2026-09-25',
		to: '2026-10-09',
		vehicle: 'guest'
	});
});

it('liest den Abgleich mit den Zählern mit GET', async () => {
	reply(checks);
	const result = await api.ledgerChecks('2026-10-02', '2026-10-08');
	const url = new URL(sent().url, 'http://x');
	expect(url.pathname).toBe('/api/ledger/checks');
	expect(Object.fromEntries(url.searchParams)).toEqual({ from: '2026-10-02', to: '2026-10-08' });
	expect(result.days.some((day) => day.flagged)).toBe(true);
});

it('liest die Sensorkandidaten für das Lastmodell mit GET', async () => {
	reply(candidates);
	const result = await api.learningCandidates();
	expect(sent()).toMatchObject({ url: '/api/learning/candidates', method: 'GET' });
	expect(result.entities.map((entity) => entity.entity_id)).toContain('sensor.aussentemperatur');
	expect(result.error).toBeNull();
});

it('liest den Stand des Lastmodells und die Güte je Tag mit GET', async () => {
	reply(learning);
	const status = await api.learningStatus();
	expect(sent()).toMatchObject({ url: '/api/learning/load', method: 'GET' });
	expect(status.method).toBe('model');

	fetchMock.mockClear();
	reply(qualityDaily);
	const daily = await api.forecastQualityDaily(28);
	expect(sent()).toMatchObject({ url: '/api/forecast/quality/daily?days=28', method: 'GET' });
	expect(daily.rows.length).toBeGreaterThan(0);
});

it('startet das Training mit POST und CSRF-Token', async () => {
	reply({ started: true });
	expect(await api.trainLoadModel()).toEqual({ started: true });
	expect(sent()).toMatchObject({
		url: '/api/learning/load/train',
		method: 'POST',
		csrf: 'tok-123'
	});
});

it('liest das Fahrverhalten und startet die Neuberechnung mit POST und CSRF-Token', async () => {
	reply(driving);
	expect((await api.drivingStatus()).vehicles.map((car) => car.key)).toEqual(['tesla', 'egolf']);
	expect(sent()).toMatchObject({ url: '/api/learning/driving', method: 'GET' });
	fetchMock.mockReset();
	reply({ started: true });
	expect(await api.recomputeDriving()).toEqual({ started: true });
	expect(sent()).toMatchObject({
		url: '/api/learning/driving/recompute',
		method: 'POST',
		csrf: 'tok-123'
	});
});

it('schaltet die Automatik eines Autos und prüft ihren Zustand', async () => {
	reply(automation);
	const state = await api.setAutomation('e-golf', true);
	expect(sent()).toEqual({
		url: '/api/control/vehicles/e-golf/automation',
		method: 'PUT',
		body: { on: true },
		csrf: 'tok-123'
	});
	expect(state.state).toBe('inactive');
});
