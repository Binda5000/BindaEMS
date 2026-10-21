<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type {
		AutomationState,
		ChargeMode,
		ControlReply,
		SupportIntent,
		SurplusPriority,
		VehicleHealth
	} from '$lib/api/schemas';
	import GuestCard from '$lib/charging/GuestCard.svelte';
	import PriorityCard from '$lib/charging/PriorityCard.svelte';
	import SupportCard from '$lib/charging/SupportCard.svelte';
	import VehicleCard from '$lib/charging/VehicleCard.svelte';
	import WallboxCard from '$lib/charging/WallboxCard.svelte';
	import {
		boostInfo,
		healthIsCurrent,
		latestSession,
		overrideVehicle,
		planFits,
		plannedLine,
		priorityOrder,
		sessionLine,
		type CardNotice
	} from '$lib/charging/view';
	import Notice from '$lib/components/Notice.svelte';
	import { socEntries } from '$lib/dashboard/socs';
	import { live } from '$lib/live.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { canOperate } from '$lib/roles';
	import { todayIso } from '$lib/stats/period';
	import { addDays } from '$lib/time';
	import { wallboxName } from '$lib/wallboxes';

	/** So lange nach einer Änderung dauert es, bis Health sie zeigt. */
	const SYSTEM_REFRESH_MS = 2000;
	/** So oft fragt die Seite den Plan höchstens neu, solange er nicht zu Health passt. */
	const PLAN_RECHECK_MS = 10_000;
	/** So viele Tage zurück sucht die Fahrzeugkarte nach der letzten Session. */
	const SESSION_DAYS = 30;
	/** Ohne Absicht ist die Akku-Unterstützung erlaubt, mit der Schwelle der Konfiguration. */
	const DEFAULT_SUPPORT: SupportIntent = { allowed: true, soc_pct: null };
	/** Ohne Absicht regelt der core nichts; Gast und Vorrang haben dann diese Werte. */
	const DEFAULT_SURPLUS: SurplusPriority = { kind: 'battery_until_soc', soc_pct: 30 };
	/** Ziel-SOC und Abfahrt eines Fahrzeugs ohne Eintrag in der Absicht (Standard des core) */
	const DEFAULT_TARGET_PCT = 80;
	const DEFAULT_DEPARTURE = '07:00';

	const system = new Resource((o) => api.system(o), { intervalMs: 15_000 });
	const control = new Resource((o) => api.control(o), { intervalMs: 15_000 });
	const limits = new Resource((o) => api.limits(o), { retryMs: 30_000 });
	// Der Plan ist eine Zugabe für „Geplant“: Ohne ihn zeigt die Karte nur, was Health weiß.
	const plan = new Resource((o) => api.plan(o), { intervalMs: 60_000 });
	// Die Mehrkosten sind eine Zugabe: schlägt ihre Abfrage fehl, fehlt nur der Satz an der Karte.
	const boostCost = new Resource((o) => api.boostCost(o), { intervalMs: 60_000 });
	// Vorschlag und Automatik sind ebenfalls eine Zugabe: ohne Antwort fehlen sie an den Karten.
	const driving = new Resource((o) => api.drivingStatus(o), { intervalMs: 60_000 });

	// Die Session je Fahrzeug ist eine Zugabe: schlägt die Abfrage fehl, fehlt nur die Zeile.
	// Die Tage werden bei jeder Abfrage neu bestimmt, damit eine lange offene Seite nicht veraltet.
	const sessions = new Resource(
		(o) => {
			const today = todayIso();
			return api.chargingSessions(addDays(today, -SESSION_DAYS), today, undefined, o);
		},
		{ intervalMs: 60_000 }
	);

	let nowMs = $state(Date.now()); // Boost „in der Zukunft“, alle 30 s neu bewertet
	let busy = $state(false);
	let notices = $state<Record<string, CardNotice | undefined>>({});
	let stopped = false;
	let refreshTimer: ReturnType<typeof setTimeout> | undefined;

	onMount(() => {
		const stops = [system, control, limits, boostCost, sessions, plan, driving].map((resource) =>
			resource.start()
		);
		const clock = setInterval(() => (nowMs = Date.now()), 30_000);
		return () => {
			stopped = true;
			stops.forEach((stop) => stop());
			clearInterval(clock);
			clearTimeout(refreshTimer);
		};
	});

	const operate = $derived(canOperate(page.data.user));
	const health = $derived(system.data?.core.health ?? null);
	const intent = $derived(control.data?.intent ?? null);
	const coreMissing = $derived(
		(control.data !== undefined && !control.data.core_connected) ||
			(system.data !== undefined && health === null)
	);
	const regulationOff = $derived(
		control.data?.core_connected === true && !(intent?.control ?? false)
	);
	// Ist Health älter als die Absicht, folgt die Anzeige der Absicht, bis Health sie übernommen hat
	const current = $derived(healthIsCurrent(health?.intent_version ?? null, intent));
	const vehicles = $derived(health?.vehicles ?? []);
	const names = $derived(Object.fromEntries(vehicles.map((vehicle) => [vehicle.id, vehicle.name])));
	// Health kennt die wirksame Schwelle erst nach dem ersten Zyklus des Akku-Sollwerts
	const supportEffective = $derived(
		health?.battery.support_soc_pct != null && health.battery.support_source !== null
			? { soc_pct: health.battery.support_soc_pct, source: health.battery.support_source }
			: null
	);
	const socs = $derived(socEntries(live.state, limits.data));
	const order = $derived(
		priorityOrder(
			intent?.priority ?? null,
			vehicles.map((vehicle) => vehicle.id)
		)
	);

	const validPlan = $derived(plan.data?.plan?.valid ? plan.data.plan : null);
	/** das Auto im gültigen Plan; `null`: der Plan nennt es nicht; `undefined`: Plan unbekannt */
	function planCar(id: string) {
		if (plan.data === undefined) return undefined;
		return validPlan?.vehicles.find((item) => item.key === id) ?? null;
	}
	// Nach einer Änderung meldet Health das neue Ziel oder die neue Abfahrt vor dem neuen Plan
	const planStale = $derived(
		vehicles.some((vehicle) => {
			const car = planCar(vehicle.id);
			return car != null && !planFits(vehicle, car);
		})
	);
	// Passt der Plan nicht zu Health, fragt die Seite ihn alle 10 s neu, bis er passt; eine neue
	// Antwort zum Plan beginnt die 10 s von vorn
	$effect(() => {
		void plan.data;
		if (!planStale) return;
		let timer: ReturnType<typeof setTimeout>;
		const recheck = () => {
			timer = setTimeout(() => {
				void plan.refresh();
				recheck();
			}, PLAN_RECHECK_MS);
		};
		recheck();
		return () => clearTimeout(timer);
	});

	/** Die Ziele der Absicht; ohne Eintrag die Standardwerte des core */
	function goalOf(id: string): {
		target_soc_pct: number;
		departure: string;
		departure_on: string | null;
	} {
		const wanted = intent?.vehicles[id];
		return {
			target_soc_pct: wanted?.target_soc_pct ?? DEFAULT_TARGET_PCT,
			departure: wanted?.departure ?? DEFAULT_DEPARTURE,
			departure_on: wanted?.departure_on ?? null
		};
	}

	/**
	 * „Geplant“ wie in Home Assistant: das Auto im gültigen Plan, sonst Health. Ohne Antwort zum Plan
	 * (`undefined`) sagt die Karte weder „Fertig um“ noch „Kein Plan“, ebenso, solange der Plan noch
	 * mit einem anderen Ziel oder einer anderen Abfahrt rechnet als Health.
	 */
	function plannedOf(vehicle: VehicleHealth) {
		const found = planCar(vehicle.id);
		const car = found && !planFits(vehicle, found) ? undefined : found;
		return plannedLine({
			mode: intent === null ? null : (intent.vehicles[vehicle.id]?.mode ?? 'pv'),
			vehicle,
			car,
			target: goalOf(vehicle.id).target_soc_pct,
			// ohne Absicht unbekannt: dann nennt die Zeile den Tag erst ab übermorgen
			departureOn: intent === null ? undefined : goalOf(vehicle.id).departure_on
		});
	}

	function vehicleLine(id: string): string | null {
		const session = latestSession(sessions.data ?? [], id);
		return session === null ? null : sessionLine(session);
	}

	function wallboxLabel(key: string): string {
		return wallboxName(key, limits.data?.wallboxes[key]);
	}

	/**
	 * Führt eine Bedienung aus: Die Ablehnung des core steht an der Karte `card`, eine Begrenzung
	 * als Hinweis. Danach lädt `control` sofort und `system` nach 2 s neu, mit ihm der Plan: Ein
	 * neuer Modus ändert den Plan, die Zeile „Fertig um …“ soll nicht bis zur nächsten Minute den
	 * alten zeigen. Ein neues Ziel oder eine neue Abfahrt plant die app erst nach dem nächsten
	 * Health neu; bis dahin fehlt die Zeile (`planStale`).
	 */
	async function act(
		card: string,
		call: () => Promise<ControlReply | AutomationState>
	): Promise<void> {
		busy = true;
		notices[card] = undefined;
		try {
			const reply = await call();
			if ('result' in reply && reply.result === 'clamped') {
				notices[card] = { level: 'warning', text: reply.details.join('\n') };
			}
		} catch (error) {
			notices[card] = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
			};
		}
		await Promise.all([control.refresh(), driving.refresh()]);
		if (!stopped) {
			clearTimeout(refreshTimer);
			refreshTimer = setTimeout(() => {
				void system.refresh();
				void plan.refresh();
			}, SYSTEM_REFRESH_MS);
		}
		busy = false;
	}
</script>

<div class="laden">
	{#if system.error}
		<Notice level="error">{system.error.detail}</Notice>
	{/if}
	{#if control.error}
		<Notice level="error">{control.error.detail}</Notice>
	{/if}
	{#if limits.error}
		<Notice level="error">Wallboxen nicht benannt: {limits.error.detail}</Notice>
	{/if}

	{#if coreMissing}
		<Notice level="warning">core nicht erreichbar.</Notice>
	{/if}

	{#if regulationOff}
		<Notice level="warning">
			Regelbetrieb ist aus. Der core regelt nichts; Modi und Boost wirken erst mit Regelbetrieb.
			{#if operate}
				<button
					type="button"
					disabled={busy}
					onclick={() => act('control', () => api.setRegulation(true))}
				>
					Regelbetrieb einschalten
				</button>
			{/if}
		</Notice>
		{#if notices.control}
			<Notice level={notices.control.level}>{notices.control.text}</Notice>
		{/if}
	{/if}

	{#if health}
		<div class="cards">
			{#each vehicles as vehicle (vehicle.id)}
				<VehicleCard
					{vehicle}
					wallbox={vehicle.wallbox === null ? null : wallboxLabel(vehicle.wallbox)}
					mode={intent?.vehicles[vehicle.id]?.mode ?? 'pv'}
					boost={boostInfo(vehicle, intent, nowMs, current)}
					soc={socs.find((entry) => entry.id === vehicle.id)}
					boostCost={boostCost.data}
					sessionLine={vehicleLine(vehicle.id)}
					{operate}
					{busy}
					notice={notices[`vehicle:${vehicle.id}`] ?? null}
					onMode={(mode) => act(`vehicle:${vehicle.id}`, () => api.setMode(vehicle.id, mode))}
					onBoost={(on) => act(`vehicle:${vehicle.id}`, () => api.setBoost(vehicle.id, on))}
					goal={goalOf(vehicle.id)}
					planned={plannedOf(vehicle)}
					onTarget={(pct, departure) =>
						act(`vehicle:${vehicle.id}`, () => api.setTarget(vehicle.id, pct, departure))}
					driving={driving.data?.vehicles.find((item) => item.key === vehicle.id) ?? null}
					onAdopt={(pct, departure, departureOn) =>
						act(`vehicle:${vehicle.id}`, () =>
							api.setTarget(vehicle.id, pct, departure, departureOn)
						)}
					onAutomation={(on) =>
						act(`vehicle:${vehicle.id}`, () => api.setAutomation(vehicle.id, on))}
				/>
			{/each}
			{#each health.wallboxes as wallbox (wallbox.id)}
				<WallboxCard
					{wallbox}
					title={wallboxLabel(wallbox.id)}
					{names}
					assigned={overrideVehicle(wallbox.id, vehicles, intent, current)}
					{operate}
					{busy}
					notice={notices[`wallbox:${wallbox.id}`] ?? null}
					onAssign={(vehicle) =>
						act(`wallbox:${wallbox.id}`, () => api.setWallboxVehicle(wallbox.id, vehicle))}
				/>
			{/each}
			<GuestCard
				mode={intent?.guest.mode ?? 'pv'}
				{operate}
				{busy}
				notice={notices.guest ?? null}
				onMode={(mode: ChargeMode) => act('guest', () => api.setGuestMode(mode))}
			/>
			{#if limits.data}
				<SupportCard
					support={intent?.support ?? DEFAULT_SUPPORT}
					effective={supportEffective}
					configured_pct={limits.data.control.support_soc_pct}
					reserve_pct={limits.data.battery.reserve_soc_pct}
					{operate}
					{busy}
					notice={notices.support ?? null}
					onChange={(allowed, socPct) => act('support', () => api.setSupport(allowed, socPct))}
				/>
			{/if}
			<PriorityCard
				{order}
				{names}
				surplus={intent?.surplus_priority ?? DEFAULT_SURPLUS}
				{operate}
				{busy}
				notice={notices.priority ?? null}
				onChange={(next, surplus) => act('priority', () => api.setPriority(next, surplus))}
			/>
		</div>
	{:else if !coreMissing && !system.error}
		<p class="muted">Lädt …</p>
	{/if}
</div>

<style>
	.laden {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.cards {
		display: grid;
		gap: var(--gap);
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
		align-items: start;
	}

	.laden :global(.notice button) {
		margin-top: 0.5rem;
	}
</style>
