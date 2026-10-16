<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { CoreHealth } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { DASH, formatDateTime, formatDay, formatEnergy, formatNumber, NBSP } from '$lib/format';
	import { live } from '$lib/live.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { canOperate, hasRole } from '$lib/roles';
	import { Acknowledgement } from '$lib/system/acknowledge.svelte';
	import ControlCard from '$lib/system/ControlCard.svelte';
	import LoadForecastCard from '$lib/system/LoadForecastCard.svelte';
	import QualityCard from '$lib/system/QualityCard.svelte';
	import SignalTable from '$lib/system/SignalTable.svelte';
	import {
		actuatorModeLabel,
		actuatorNotes,
		componentLabel,
		heartbeatLabel,
		modeLabel,
		selfcheckLabel,
		signalRows,
		vatText,
		watchdogStateLabel,
		watchdogVersionText
	} from '$lib/system/view';
	import { todayVienna } from '$lib/time';

	const uid = $props.id();
	const system = new Resource((o) => api.system(o), { intervalMs: 15_000 });
	const prices = new Resource((o) => api.prices(null, o), { intervalMs: 300_000 });
	const forecast = new Resource((o) => api.forecast(o), { intervalMs: 300_000 });
	const loadForecast = new Resource((o) => api.loadForecast(o), { intervalMs: 300_000 });
	const quality = new Resource((o) => api.forecastQuality(14, o), { intervalMs: 300_000 });
	const appHealth = new Resource((o) => api.health(o));
	const control = new Resource((o) => api.control(o), { intervalMs: 15_000 });

	let nowMs = $state(Date.now()); // Alter der Signale zählt jede Sekunde weiter
	let filter = $state('');
	let followUp: ReturnType<typeof setTimeout> | undefined;

	onMount(() => {
		const stops = [system, prices, forecast, loadForecast, quality, appHealth, control].map(
			(resource) => resource.start()
		);
		const clock = setInterval(() => (nowMs = Date.now()), 1000);
		return () => {
			clearTimeout(followUp);
			stops.forEach((stop) => stop());
			clearInterval(clock);
		};
	});

	const admin = $derived(hasRole(page.data.user, 'admin'));
	const operate = $derived(canOperate(page.data.user));
	const health = $derived(system.data?.core.health ?? null);
	const signals = $derived(signalRows(live.state, nowMs, filter));
	const forecastDays = $derived(
		(forecast.data?.days ?? []).filter((day) => day.date >= todayVienna()).slice(0, 3)
	);

	const ORIGINS = { primary: 'smartENERGY', fallback: 'Ersatzquelle' } as const;
	const SELFCHECK_STATE = { ok: 'ok', warn: 'warn', fail: 'error', unknown: 'unknown' } as const;
	const SELFCHECK_TEXT = {
		ok: 'in Ordnung',
		warn: 'Warnung',
		fail: 'Fehler',
		unknown: 'unbekannt'
	};

	const WATCHDOG_DOT = {
		armed: 'ok',
		waiting: 'warn',
		disabled: 'warn',
		tripped: 'error'
	} as const;

	/** Ohne Antwort ist der Watchdog ein Fehler, sobald der Betrieb ihn braucht. */
	function watchdogDot(watchdog: CoreHealth['watchdog']): 'ok' | 'warn' | 'error' | 'unknown' {
		if (watchdog.state === 'unknown') return watchdog.required ? 'error' : 'unknown';
		return WATCHDOG_DOT[watchdog.state];
	}

	let refreshing = $state(false);
	let refreshMessage = $state<{ level: 'info' | 'error'; text: string } | null>(null);

	async function refreshPrices() {
		refreshing = true;
		refreshMessage = null;
		try {
			const status = await api.refreshPrices();
			refreshMessage =
				status.errors.length > 0
					? { level: 'error', text: status.errors.join('\n') }
					: { level: 'info', text: 'Preise abgerufen.' };
			await prices.refresh();
		} catch (error) {
			refreshMessage = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
			};
		} finally {
			refreshing = false;
		}
	}

	/** Nach einer Bedienung: die Absicht gleich, Health (der Stand der Aktoren) nach 2 s noch einmal. */
	async function changed() {
		await Promise.all([control.refresh(), system.refresh()]);
		clearTimeout(followUp);
		followUp = setTimeout(() => void system.refresh(), 2000);
	}

	const acknowledgement = new Acknowledgement(() => system.refresh());

	function since(iso: string | null): string {
		return iso === null ? '' : `seit ${formatDateTime(iso)}`;
	}
</script>

<div class="system">
	{#if system.error}
		<Notice level="error">{system.error.detail}</Notice>
	{/if}

	<Card title="Komponenten">
		{#if system.data}
			<ul class="components">
				{#each system.data.components as component (component.name)}
					<li>
						<StatusDot
							state={component.ok ? 'ok' : 'error'}
							label={component.ok ? 'in Ordnung' : 'gestört'}
						/>
						<span class="label">{componentLabel(component.name)}</span>
						<span class="message">{component.message}</span>
						{#if component.since}
							<span class="since muted">{since(component.since)}</span>
						{/if}
					</li>
				{/each}
			</ul>
		{:else if !system.error}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>

	<Card title="Hinweise">
		{#if system.data && system.data.warnings.length > 0}
			<ul class="plain">
				{#each system.data.warnings as warning, index (index)}
					<li><Notice level="warning">{warning}</Notice></li>
				{/each}
			</ul>
		{:else}
			<p class="muted">Keine Hinweise</p>
		{/if}
	</Card>

	<Card title="core">
		<dl class="facts">
			<dt>Verbindung</dt>
			<dd>{system.data?.core.connected ? 'verbunden' : 'getrennt'}</dd>
			<dt>Betriebsart</dt>
			<dd>
				{#if health}
					{modeLabel(health.mode)}
					{#if health.mode_since}
						<span class="muted">{since(health.mode_since)}</span>
					{/if}
				{:else}
					{DASH}
				{/if}
			</dd>
			{#if health && health.mode_reasons.length > 0}
				<dd class="list">
					<ul>
						{#each health.mode_reasons as reason, index (index)}
							<li>{reason}</li>
						{/each}
					</ul>
				</dd>
			{/if}
			<dt>Eingangsdaten</dt>
			{#if health && !health.inputs.ok}
				<dd class="list">
					<ul>
						{#each health.inputs.problems as problem, index (index)}
							<li>{problem}</li>
						{/each}
					</ul>
				</dd>
			{:else}
				<dd>{health ? 'in Ordnung' : DASH}</dd>
			{/if}
			<dt>Version core</dt>
			<dd>{health?.version ?? DASH}</dd>
			<dt>Version app</dt>
			<dd>{appHealth.data?.version ?? DASH}</dd>
			<dt>Zykluszeit (p95)</dt>
			<dd>
				{health?.cycle_ms_p95 == null ? DASH : `${formatNumber(health.cycle_ms_p95, 1)}${NBSP}ms`}
			</dd>
		</dl>

		{#if health}
			<h3>Adapter</h3>
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th scope="col">Adapter</th>
							<th scope="col">Verbunden</th>
							<th scope="col">Letzter Erfolg</th>
							<th scope="col">Letzter Fehler</th>
							<th scope="col" class="num">Fehler</th>
						</tr>
					</thead>
					<tbody>
						{#each health.adapters as adapter (adapter.name)}
							<tr>
								<td>{adapter.name}</td>
								<td>
									<StatusDot
										state={adapter.connected ? 'ok' : 'error'}
										label={adapter.connected ? 'verbunden' : 'getrennt'}
									/>
									{adapter.connected ? 'ja' : 'nein'}
								</td>
								<td>{adapter.last_ok ? formatDateTime(adapter.last_ok) : DASH}</td>
								<td>{adapter.last_error ?? DASH}</td>
								<td class="num">{adapter.error_count}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<h3>Selbstprüfung</h3>
			<ul class="checks">
				{#each health.selfcheck as check (check.id)}
					<li>
						<StatusDot state={SELFCHECK_STATE[check.status]} label={SELFCHECK_TEXT[check.status]} />
						<span class="label">{selfcheckLabel(check.id)}</span>
						<span class="message">{check.message}</span>
					</li>
				{/each}
			</ul>

			<h3>Alarme</h3>
			{#if health.alarms.length > 0}
				<ul class="plain">
					{#each health.alarms as alarm (alarm.id)}
						<li>
							<Notice level={alarm.severity === 'info' ? 'info' : alarm.severity}>
								{alarm.message} <span class="muted">({since(alarm.since)})</span>
							</Notice>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="muted">Keine Alarme</p>
			{/if}
		{/if}
	</Card>

	{#if health}
		<ControlCard
			{health}
			intent={control.data?.intent ?? null}
			{operate}
			{admin}
			onChanged={changed}
		/>
	{/if}

	<Card title="Aktoren">
		{#if health}
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th scope="col">Aktor</th>
							<th scope="col">Angefordert</th>
							<th scope="col">Wirksam</th>
							<th scope="col">live erlaubt</th>
							<th scope="col">Hinweise</th>
						</tr>
					</thead>
					<tbody>
						{#each health.actuators as actuator (actuator.id)}
							{@const notes = actuatorNotes(actuator)}
							<tr>
								<td id="{uid}-{actuator.id}">{actuator.label}</td>
								<td>{actuatorModeLabel(actuator.requested)}</td>
								<td>{actuatorModeLabel(actuator.effective)}</td>
								<td>{actuator.live_allowed ? 'ja' : 'nein'}</td>
								<td class="notes">
									{#if notes.length > 0}
										<ul class="plain">
											{#each notes as note, index (index)}
												<li>{note}</li>
											{/each}
										</ul>
									{:else}
										{DASH}
									{/if}
									{#if admin && actuator.locked.length > 0}
										<button
											type="button"
											class="ack"
											disabled={acknowledgement.busy}
											aria-describedby="{uid}-{actuator.id}"
											onclick={() => acknowledgement.run(actuator.id)}
										>
											Quittieren
										</button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if acknowledgement.notice}
				<Notice level={acknowledgement.notice.level}>{acknowledgement.notice.text}</Notice>
			{/if}
		{:else if system.data}
			<p class="muted">Keine Daten vom core</p>
		{/if}
	</Card>

	<Card title="Watchdog am Cerbo">
		{#if health}
			{@const watchdog = health.watchdog}
			<dl class="facts">
				<dt>Zustand</dt>
				<dd class="state">
					<StatusDot state={watchdogDot(watchdog)} label={watchdogStateLabel(watchdog.state)} />
					<span>{watchdogStateLabel(watchdog.state)}</span>
				</dd>
				<dt>Scharf</dt>
				<dd>{watchdog.armed ? 'ja' : 'nein'}</dd>
				<dt>Heartbeat</dt>
				<dd>{heartbeatLabel(watchdog.heartbeat)}</dd>
				<dt>Auslösungen</dt>
				<dd>{watchdog.trip_count ?? DASH}</dd>
				<dt>Letzte Auslösung</dt>
				<dd>
					{#if watchdog.last_trip}
						{formatDateTime(watchdog.last_trip)}
					{:else}
						<!-- „nie“ nur, wenn der Zähler es sagt; ohne Antwort des Watchdogs ist es unbekannt -->
						{watchdog.trip_count === 0 ? 'nie' : DASH}
					{/if}
				</dd>
				<dt>Fehlgeschlagene Aktionen</dt>
				<dd>{watchdog.last_trip_failures ?? DASH}</dd>
				<dt>Version</dt>
				<dd>{watchdogVersionText(watchdog.version, watchdog.expected_version)}</dd>
			</dl>
		{:else if system.data}
			<p class="muted">Keine Daten vom core</p>
		{/if}
	</Card>

	<Card title="Preise">
		{#if prices.error}
			<Notice level="error">{prices.error.detail}</Notice>
		{/if}
		{#if prices.data}
			{@const status = prices.data.status}
			<dl class="facts">
				<dt>Letzter Versuch</dt>
				<dd>{status.last_attempt ? formatDateTime(status.last_attempt) : DASH}</dd>
				<dt>Letzter Erfolg</dt>
				<dd>{status.last_success ? formatDateTime(status.last_success) : DASH}</dd>
				<dt>Netto/Brutto</dt>
				<dd>{vatText(status)}</dd>
			</dl>
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th scope="col">Tag</th>
							<th scope="col">Herkunft</th>
							<th scope="col">Befunde</th>
						</tr>
					</thead>
					<tbody>
						{#each status.days as day (day.date)}
							<tr>
								<td>{formatDay(day.date)}</td>
								<td>{day.origin ? ORIGINS[day.origin] : 'keine Preise'}</td>
								<td>{day.findings.length > 0 ? day.findings.join('; ') : DASH}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#each status.errors as message, index (index)}
				<Notice level="error">{message}</Notice>
			{/each}
		{/if}
		{#if admin}
			<p class="refresh">
				<button type="button" disabled={refreshing} onclick={refreshPrices}>
					Preise jetzt abrufen
				</button>
				<span class="muted">kann bis zu 30 s dauern</span>
			</p>
			{#if refreshMessage}
				<Notice level={refreshMessage.level}>{refreshMessage.text}</Notice>
			{/if}
		{/if}
	</Card>

	<Card title="PV-Prognose">
		{#if forecast.error}
			<Notice level="error">{forecast.error.detail}</Notice>
		{/if}
		{#if forecast.data}
			<p class="status">
				<StatusDot
					state={forecast.data.status.ok ? 'ok' : 'error'}
					label={forecast.data.status.ok ? 'in Ordnung' : 'gestört'}
				/>
				{forecast.data.status.message}
			</p>
			<dl class="facts">
				<dt>Ausgegeben</dt>
				<dd>{forecast.data.issued_at ? formatDateTime(forecast.data.issued_at) : DASH}</dd>
				{#each forecastDays as day (day.date)}
					<dt>{formatDay(day.date)}</dt>
					<dd>{formatEnergy(day.kwh)}</dd>
				{/each}
			</dl>
		{/if}
	</Card>

	<LoadForecastCard data={loadForecast.data} error={loadForecast.error} />

	<QualityCard data={quality.data} error={quality.error} />

	<Card title="Signale">
		<label class="filter">
			Signal suchen
			<input type="search" bind:value={filter} autocapitalize="none" spellcheck="false" />
		</label>
		<SignalTable rows={signals} />
	</Card>
</div>

<style>
	.facts {
		margin: 0 0 0.75rem;
	}

	.system {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	h3 {
		margin: 1.25rem 0 0.5rem;
		font-size: 0.9375rem;
	}

	.components,
	.checks,
	.plain {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.5rem;
	}

	.components li,
	.checks li {
		display: grid;
		grid-template-columns: auto minmax(7rem, auto) 1fr;
		align-items: baseline;
		gap: 0.25rem 0.75rem;
	}

	.components .since {
		grid-column: 3;
		font-size: 0.875rem;
	}

	.label {
		font-weight: 600;
	}

	/* Liste unter einer Zeile der Kennzahlen: in der Spalte der Werte, auf schmalen Schirmen über
	   die ganze Breite (die Spalte der Werte wäre dort zu schmal für ganze Sätze) */
	.facts .list {
		grid-column: 2;
	}

	.list ul {
		margin: 0;
		padding-left: 1.25rem;
	}

	.state {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.notes {
		min-width: 12rem;
	}

	.ack {
		margin-top: 0.5rem;
	}

	.scroll {
		overflow-x: auto;
	}

	.refresh {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
		margin: 1rem 0 0.5rem;
	}

	.status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0 0 0.75rem;
	}

	.filter {
		display: grid;
		gap: 0.25rem;
		max-width: 24rem;
		margin-bottom: 0.75rem;
		font-weight: 600;
	}

	@media (max-width: 40rem) {
		.components li,
		.checks li {
			grid-template-columns: auto 1fr;
		}

		.components .message,
		.checks .message,
		.components .since {
			grid-column: 2;
		}

		.facts .list {
			grid-column: 1 / -1;
		}
	}
</style>
