<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import type { ChargingSession, Limits, Stats } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { statsEnergyOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { Resource } from '$lib/resource.svelte';
	import ChecksCard from '$lib/stats/ChecksCard.svelte';
	import ConsumersCard from '$lib/stats/ConsumersCard.svelte';
	import CostCard from '$lib/stats/CostCard.svelte';
	import {
		PERIODS,
		isRunning,
		parseDay,
		parsePeriod,
		periodParam,
		periodRange,
		periodTitle,
		shift,
		todayIso,
		type Period
	} from '$lib/stats/period';
	import PeaksCard from '$lib/stats/PeaksCard.svelte';
	import SessionsCard from '$lib/stats/SessionsCard.svelte';
	import StatTiles from '$lib/stats/StatTiles.svelte';
	import VehicleCard from '$lib/stats/VehicleCard.svelte';
	import { hints, tiles } from '$lib/stats/view';
	import { theme } from '$lib/theme.svelte';
	import { wallboxName } from '$lib/wallboxes';

	const POLL_MS = 60_000;
	const RETRY_MS = 30_000;

	// Wiener Datum, jede Minute neu: „heute“ und der laufende Zeitraum rücken um Mitternacht nach
	let today = $state(todayIso());
	onMount(() => {
		const clock = setInterval(() => (today = todayIso()), 60_000);
		return () => clearInterval(clock);
	});

	// Zeitraum und Datum stehen in der URL (?zeitraum=tag|woche|monat|jahr&datum=JJJJ-MM-TT)
	const period = $derived(parsePeriod(page.url.searchParams.get('zeitraum')));
	const day = $derived(parseDay(page.url.searchParams.get('datum'), today));
	const title = $derived(periodTitle(period, day));
	const range = $derived(periodRange(period, day));
	const running = $derived(isRunning(period, day, today));

	function open(nextPeriod: Period, nextDay: string) {
		void goto(`/statistik?zeitraum=${periodParam(nextPeriod)}&datum=${nextDay}`, {
			keepFocus: true,
			noScroll: true
		});
	}

	// Der laufende Zeitraum lädt jede Minute nach, ein vergangener nur einmal (nach einem Fehler
	// alle 30 s erneut). Das gilt für die Statistik wie für die Ladesessions. Eine Abfrage merkt
	// sich Titel und „läuft“ ihres Zeitraums, damit Hinweise und Diagramm zu ihren Werten passen.
	interface Query {
		title: string;
		running: boolean;
		stats: Resource<Stats>;
		sessions: Resource<ChargingSession[]>;
	}
	let query = $state.raw<Query | null>(null);
	$effect(() => {
		const key = { period, day, first: range.first, last: range.last };
		const options = { intervalMs: running ? POLL_MS : undefined, retryMs: RETRY_MS };
		const next: Query = {
			title,
			running,
			stats: new Resource((o) => api.stats(key.period, key.day, o), options),
			sessions: new Resource(
				(o) => api.chargingSessions(key.first, key.last, undefined, o),
				options
			)
		};
		query = next;
		const stopStats = next.stats.start();
		const stopSessions = next.sessions.start();
		return () => {
			stopStats();
			stopSessions();
		};
	});

	// Jeder Zeitraum hat seine eigene Abfrage. Beim Blättern bleibt bis zur Antwort der Statistik
	// die vorige Abfrage mit ihren Werten gedimmt stehen, Statistik und Sessions zusammen
	// (`aria-busy`); nur vor der allerersten Antwort steht „Lädt …“. Scheitert die Abfrage, steht
	// der Fehler, und beim Weiterblättern steht danach nichts Altes mehr da.
	let shown = $state.raw<Query | undefined>(undefined);
	$effect(() => {
		if (query?.stats.data !== undefined) shown = query;
		else if (query?.stats.error) shown = undefined;
	});
	const view = $derived(
		query?.stats.data !== undefined ? query : query?.stats.error ? undefined : shown
	);
	const current = $derived(view?.stats.data);
	// gedimmt: die Werte gehören noch zum vorigen Zeitraum
	const stale = $derived(view !== undefined && view !== query);
	const busy = $derived(
		stale || (view !== undefined && view.sessions.data === undefined && !view.sessions.error)
	);

	// Anzeigenamen der Wallboxes für den Abgleich; ohne Antwort gilt der Schlüssel
	const limits = new Resource<Limits>((o) => api.limits(o), { retryMs: RETRY_MS });
	onMount(() => limits.start());
	const wallboxNames = $derived(
		Object.fromEntries(
			Object.entries(limits.data?.wallboxes ?? {}).map(([key, box]) => [key, wallboxName(key, box)])
		)
	);

	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const chartOption = $derived(current ? statsEnergyOption(current, palette) : null);
</script>

<div class="stats">
	<Card>
		<div class="switch" role="group" aria-label="Zeitraum wählen">
			{#each PERIODS as item (item.id)}
				<button
					type="button"
					class:active={period === item.id}
					aria-pressed={period === item.id}
					onclick={() => open(item.id, day)}
				>
					{item.label}
				</button>
			{/each}
		</div>
		<div class="paging">
			<button
				type="button"
				aria-label="Zurück"
				onclick={() => open(period, shift(period, day, -1))}
			>
				←
			</button>
			<h2>{title}</h2>
			<button
				type="button"
				aria-label="Weiter"
				disabled={range.last >= today}
				onclick={() => open(period, shift(period, day, 1))}
			>
				→
			</button>
			<button type="button" disabled={running} onclick={() => open(period, today)}>Heute</button>
		</div>
		{#if current}
			<ul class="hints muted" class:stale>
				{#each hints(current, view?.running ?? false) as hint (hint)}
					<li>{hint}</li>
				{/each}
			</ul>
		{/if}
	</Card>

	{#if query?.stats.error}
		<Notice level="error">{query.stats.error.detail}</Notice>
	{/if}

	{#if current}
		<div class="results" class:stale aria-busy={busy}>
			<StatTiles tiles={tiles(current)} />

			<Card title="Energie">
				{#if chartOption}
					<EChart option={chartOption} label="Energie {view?.title}" height="20rem" />
				{/if}
			</Card>

			<CostCard stats={current} />
			<VehicleCard stats={current} />
			<SessionsCard sessions={view?.sessions.data} error={view?.sessions.error?.detail} />
			<ConsumersCard stats={current} />
			<PeaksCard stats={current} />
			<ChecksCard stats={current} {wallboxNames} />
		</div>
	{:else if !query?.stats.error}
		<p class="muted">Lädt …</p>
	{/if}
</div>

<style>
	.stats {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.results {
		display: grid;
		gap: var(--gap);
		min-width: 0;
		transition: opacity 0.2s;
	}

	.stale {
		opacity: 0.5;
	}

	.switch {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
	}

	.switch .active {
		border-color: var(--accent);
		color: var(--accent);
		font-weight: 600;
	}

	.paging {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}

	.paging h2 {
		flex: 1 1 10rem;
		min-width: 0;
		margin: 0;
		font-size: 1.125rem;
		font-weight: 600;
		text-align: center;
	}

	.hints {
		display: flex;
		flex-wrap: wrap;
		margin: 0.5rem 0 0;
		padding: 0;
		list-style: none;
		font-size: 0.875rem;
	}

	.hints li + li::before {
		content: '·';
		margin: 0 0.5rem;
	}
</style>
