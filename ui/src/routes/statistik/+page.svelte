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
	// alle 30 s erneut). Das gilt für die Statistik wie für die Ladesessions.
	let resource = $state.raw<Resource<Stats> | null>(null);
	let sessions = $state.raw<Resource<ChargingSession[]> | null>(null);
	$effect(() => {
		const query = { period, day, first: range.first, last: range.last };
		const options = { intervalMs: running ? POLL_MS : undefined, retryMs: RETRY_MS };
		const nextStats = new Resource((o) => api.stats(query.period, query.day, o), options);
		const nextSessions = new Resource(
			(o) => api.chargingSessions(query.first, query.last, undefined, o),
			options
		);
		resource = nextStats;
		sessions = nextSessions;
		const stopStats = nextStats.start();
		const stopSessions = nextSessions.start();
		return () => {
			stopStats();
			stopSessions();
		};
	});

	// Jeder Zeitraum hat seine eigene Abfrage: beim Blättern steht bis zur Antwort „Lädt …“
	const current = $derived(resource?.data);

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
			<ul class="hints muted">
				{#each hints(current, running) as hint (hint)}
					<li>{hint}</li>
				{/each}
			</ul>
		{/if}
	</Card>

	{#if resource?.error}
		<Notice level="error">{resource.error.detail}</Notice>
	{/if}

	{#if current}
		<StatTiles tiles={tiles(current)} />

		<Card title="Energie">
			{#if chartOption}
				<EChart option={chartOption} label="Energie {title}" height="20rem" />
			{/if}
		</Card>

		<CostCard stats={current} />
		<VehicleCard stats={current} />
		<SessionsCard sessions={sessions?.data} error={sessions?.error?.detail} />
		<ConsumersCard stats={current} />
		<PeaksCard stats={current} />
		<ChecksCard stats={current} {wallboxNames} />
	{:else if !resource?.error}
		<p class="muted">Lädt …</p>
	{/if}
</div>

<style>
	.stats {
		display: grid;
		gap: var(--gap);
		min-width: 0;
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
