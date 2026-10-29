<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import type { DaySummary, HistoryResponse } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { historyOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { wallboxLabels } from '$lib/dashboard/flow';
	import BalanceTable from '$lib/history/BalanceTable.svelte';
	import { balanceRows } from '$lib/history/balance';
	import {
		DEFAULT_SERIES,
		MAX_DAYS,
		MAX_SERIES,
		PRESETS,
		dayCount,
		isToday,
		presetDays,
		rangeQuery,
		type DayRange,
		type RangePreset
	} from '$lib/history/ranges';
	import SeriesPicker from '$lib/history/SeriesPicker.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { theme } from '$lib/theme.svelte';
	import { todayVienna } from '$lib/time';
	import { seriesLabel, uniqueDisplays } from '$lib/wallboxes';

	const POLL_MS = 60_000;

	let preset = $state<RangePreset | null>('heute');
	let first = $state(presetDays('heute', new Date()).first);
	let last = $state(presetDays('heute', new Date()).last);

	const catalog = new Resource((o) => api.historyCatalog(o));
	const limits = new Resource((o) => api.limits(o));

	// Wiener Datum, jede Minute neu: eine Voreinstellung (Heute, 7 Tage …) rückt um Mitternacht
	// nach, sonst zeigte eine über Nacht offene Seite den Vortag als „Heute“
	let today = $state(todayVienna());
	onMount(() => {
		const stops = [catalog.start(), limits.start()];
		const clock = setInterval(() => (today = todayVienna()), 60_000);
		return () => {
			stops.forEach((stop) => stop());
			clearInterval(clock);
		};
	});
	$effect(() => {
		void today;
		if (preset === null) return;
		const days = presetDays(preset, new Date());
		if (days.first !== first) first = days.first;
		if (days.last !== last) last = days.last;
	});

	// Reihen aus der URL (?reihen=grid,pv), damit Links die Auswahl behalten
	const selected = $derived.by(() => {
		const param = page.url.searchParams.get('reihen');
		const ids = param === null ? [...DEFAULT_SERIES] : param.split(',').filter(Boolean);
		return ids.slice(0, MAX_SERIES);
	});

	function selectSeries(ids: string[]) {
		const url = new URL(page.url);
		url.searchParams.set('reihen', ids.join(','));
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function choose(id: RangePreset) {
		const days = presetDays(id, new Date());
		preset = id;
		first = days.first;
		last = days.last;
	}

	const rangeError = $derived.by(() => {
		if (!first || !last) return 'Bitte ersten und letzten Tag wählen.';
		if (first > last) return 'Der erste Tag liegt nach dem letzten.';
		if (dayCount({ first, last }) > MAX_DAYS) return `Höchstens ${MAX_DAYS} Tage.`;
		return null;
	});

	const days = $derived<DayRange | null>(rangeError === null ? { first, last } : null);

	// Für einen Zeitraum mit heute laden Diagramm und Bilanz jede Minute nach
	let history = $state.raw<Resource<HistoryResponse> | null>(null);
	$effect(() => {
		const range = days;
		const series = selected;
		if (range === null || series.length === 0) {
			history = null;
			return;
		}
		const query = rangeQuery(range);
		const resource = new Resource((o) => api.history(series, query, o), {
			intervalMs: isToday(range, new Date()) ? POLL_MS : undefined
		});
		history = resource;
		return resource.start();
	});

	let ledger = $state.raw<Resource<DaySummary[]> | null>(null);
	$effect(() => {
		const range = days;
		if (range === null) {
			ledger = null;
			return;
		}
		const resource = new Resource((o) => api.ledgerDays(range.first, range.last, o), {
			intervalMs: isToday(range, new Date()) ? POLL_MS : undefined
		});
		ledger = resource;
		return resource.start();
	});

	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	// Wallboxen heißen überall wie in den Einstellungen, nicht „Wallbox evcs“
	const wallboxNames = $derived(wallboxLabels(limits.data));
	// gleich benannte Wallboxen bleiben als zwei Reihen unterscheidbar
	const seriesNames = $derived(uniqueDisplays(catalog.data ?? [], wallboxNames));
	const catalogEntries = $derived(
		catalog.data?.map((entry) => ({ ...entry, label: seriesNames[entry.id] ?? entry.label }))
	);
	const historyData = $derived.by(() => {
		const data = history?.data;
		if (!data) return null;
		const series = Object.fromEntries(
			Object.entries(data.series).map(([id, entry]) => [
				id,
				{ ...entry, label: seriesNames[id] ?? seriesLabel(id, entry.label, wallboxNames) }
			])
		);
		return { ...data, series };
	});
	const chartOption = $derived(historyData ? historyOption(historyData, palette) : null);
	const rows = $derived(ledger?.data ? balanceRows(ledger.data, wallboxNames) : []);
	const chartLabel = $derived(
		`Verlauf von ${first} bis ${last}: ${
			catalogEntries
				?.filter((entry) => selected.includes(entry.id))
				.map((entry) => entry.label)
				.join(', ') ?? ''
		}`
	);
</script>

<div class="history">
	<Card title="Zeitraum">
		<div class="range">
			<div class="presets" role="group" aria-label="Zeitraum wählen">
				{#each PRESETS as item (item.id)}
					<button
						type="button"
						class:active={preset === item.id}
						aria-pressed={preset === item.id}
						onclick={() => choose(item.id)}
					>
						{item.label}
					</button>
				{/each}
			</div>
			<div class="dates">
				<label>
					Erster Tag
					<input type="date" bind:value={first} oninput={() => (preset = null)} />
				</label>
				<label>
					Letzter Tag
					<input type="date" bind:value={last} oninput={() => (preset = null)} />
				</label>
			</div>
		</div>
		{#if rangeError}
			<p class="error" role="alert">{rangeError}</p>
		{/if}
		{#if catalogEntries}
			<SeriesPicker catalog={catalogEntries} {selected} onchange={selectSeries} />
		{:else if catalog.error}
			<Notice level="error">{catalog.error.detail}</Notice>
		{/if}
	</Card>

	<Card title="Diagramm">
		{#if history?.error}
			<Notice level="error">{history.error.detail}</Notice>
		{/if}
		{#if chartOption}
			<EChart option={chartOption} label={chartLabel} height="22rem" />
		{:else if selected.length === 0}
			<p class="muted">Keine Reihe gewählt.</p>
		{:else if !history?.error && !rangeError}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>

	<Card title="Tagesbilanz">
		{#if ledger?.error}
			<Notice level="error">{ledger.error.detail}</Notice>
		{/if}
		{#if rows.length > 0}
			<BalanceTable {rows} />
		{:else if !ledger?.error && !rangeError}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>
</div>

<style>
	.history {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.range {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem 1.5rem;
		align-items: end;
		margin-bottom: 1rem;
	}

	.presets {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.presets .active {
		border-color: var(--accent);
		color: var(--accent);
		font-weight: 600;
	}

	.dates {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	.dates label {
		display: grid;
		gap: 0.25rem;
		font-size: 0.875rem;
		color: var(--muted);
	}

	.error {
		color: var(--error);
		margin: 0 0 1rem;
	}
</style>
