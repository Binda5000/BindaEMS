<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { Decision, DecisionQuery } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { wallboxLabels } from '$lib/dashboard/flow';
	import DecisionTable from '$lib/decisions/DecisionTable.svelte';
	import {
		RANGES,
		RESULT_IDS,
		decisionQuery,
		filtersFromParams,
		filtersToParams,
		isDate,
		resultLabel,
		type Filters
	} from '$lib/decisions/view';
	import { Resource } from '$lib/resource.svelte';
	import { todayVienna } from '$lib/time';
	import { actuatorDisplay } from '$lib/wallboxes';

	// Die Filter stehen in der URL (Links aus „Warum?“ öffnen gefiltert): Die Seite liest sie beim
	// Öffnen und bei jeder Änderung der URL von außen (Navigation, Zurück/Vor) und schreibt jede
	// eigene Änderung zurück
	const initial = filtersFromParams(page.url.searchParams);
	let filters = $state<Filters>(initial);
	let text = $state(initial.text); // Eingabe, gilt erst mit dem Verlassen des Feldes

	let rows = $state.raw<Decision[]>([]);
	let more = $state(false);
	let loading = $state(true);
	let loadingMore = $state(false);
	let error = $state.raw<ApiError | null>(null);
	let query: DecisionQuery = { limit: 0 }; // die Abfrage der ersten Seite, auch für „Ältere laden“
	let controller: AbortController | undefined;
	let written: string[] = []; // Suchteile, die die Seite selbst in die URL schrieb und noch erwartet

	const system = new Resource((o) => api.system(o), { retryMs: 30_000 });
	const limits = new Resource((o) => api.limits(o), { retryMs: 30_000 });
	onMount(() => {
		const stops = [system.start(), limits.start()];
		return () => stops.forEach((stop) => stop());
	});
	const wallboxNames = $derived(wallboxLabels(limits.data));

	const actuators = $derived(system.data?.core.health?.actuators ?? []);
	const names = $derived(Object.fromEntries(actuators.map((a) => [a.id, a.label])));
	// ein Aktor aus der URL, den Health nicht (mehr) kennt, bleibt wählbar
	const actuatorOptions = $derived(
		filters.actuator === '' || actuators.some((a) => a.id === filters.actuator)
			? actuators
			: [...actuators, { id: filters.actuator, label: filters.actuator }]
	);
	const rangeChoice = $derived(isDate(filters.range) ? 'date' : filters.range);
	const last = $derived(rows.at(-1));

	function currentSearch(): string {
		return page.url.search.replace(/^\?/, '');
	}

	function fail(e: unknown): ApiError {
		return e instanceof ApiError ? e : new ApiError(0, e instanceof Error ? e.message : String(e));
	}

	// Neu laden, sobald sich ein Filter ändert
	$effect(() => {
		const current = { ...filters };
		const run = new AbortController();
		controller?.abort();
		controller = run;
		untrack(() => {
			loading = true;
			loadingMore = false;
			query = decisionQuery(current, new Date());
		});
		api
			.decisions(query, { signal: run.signal })
			.then((response) => {
				if (run.signal.aborted) return;
				rows = response.decisions;
				more = response.more;
				error = null;
			})
			.catch((e: unknown) => {
				if (run.signal.aborted) return;
				rows = [];
				more = false;
				error = fail(e);
			})
			.finally(() => {
				if (!run.signal.aborted) loading = false;
			});
		return () => run.abort();
	});

	// Filter zurück in die URL (die URL selbst gilt hier nicht als Abhängigkeit)
	$effect(() => {
		const search = filtersToParams(filters).toString();
		untrack(() => {
			if (search === currentSearch()) return;
			written.push(search);
			const target = search === '' ? page.url.pathname : `${page.url.pathname}?${search}`;
			void goto(target, { replaceState: true, keepFocus: true, noScroll: true });
		});
	});

	// Die URL ändert sich von außen: Filter daraus neu setzen. Was die Seite selbst schrieb, ist
	// schon gesetzt (die Antwort von `goto` kann hinter einer neueren Eingabe zurückliegen).
	$effect(() => {
		const search = currentSearch();
		untrack(() => {
			const index = written.indexOf(search);
			if (index >= 0) {
				written = written.slice(index + 1);
				return;
			}
			written = [];
			const next = filtersFromParams(new URLSearchParams(search));
			if (filtersToParams(next).toString() === filtersToParams(filters).toString()) return;
			filters = next;
			text = next.text;
		});
	});

	async function loadOlder() {
		const before = last?.cursor;
		if (before === undefined || loadingMore) return;
		// ändert sich ein Filter, bricht die neue Ladung `run` ab: der alte Cursor hängt nichts mehr an
		const run = controller;
		loadingMore = true;
		try {
			const response = await api.decisions({ ...query, before }, { signal: run?.signal });
			if (run?.signal.aborted) return;
			rows = [...rows, ...response.decisions];
			more = response.more;
			error = null;
		} catch (e) {
			if (run?.signal.aborted) return;
			error = fail(e);
		} finally {
			if (!run?.signal.aborted) loadingMore = false;
		}
	}

	function chooseRange(value: string) {
		filters.range = value === 'date' ? todayVienna() : value;
	}
</script>

<div class="decisions">
	<Card title="Filter">
		<div class="filters">
			<label>
				Zeitraum
				<select value={rangeChoice} onchange={(e) => chooseRange(e.currentTarget.value)}>
					{#each RANGES as item (item.id)}
						<option value={item.id}>{item.label}</option>
					{/each}
					<option value="date">Eigenes Datum</option>
				</select>
			</label>
			{#if rangeChoice === 'date'}
				<label>
					Datum
					<input
						type="date"
						value={filters.range}
						onchange={(e) => {
							if (e.currentTarget.value !== '') filters.range = e.currentTarget.value;
						}}
					/>
				</label>
			{/if}
			<label>
				Aktor
				<select bind:value={filters.actuator}>
					<option value="">Alle</option>
					{#each actuatorOptions as actuator (actuator.id)}
						<option value={actuator.id}>
							{actuatorDisplay(actuator.id, actuator.label, wallboxNames)}
						</option>
					{/each}
				</select>
			</label>
			<label>
				Ergebnis
				<select bind:value={filters.result}>
					<option value="">Alle</option>
					{#each RESULT_IDS as id (id)}
						<option value={id}>{resultLabel(id)}</option>
					{/each}
				</select>
			</label>
			<label>
				Suche im Text
				<input type="search" bind:value={text} onchange={() => (filters.text = text.trim())} />
			</label>
			<label class="check">
				<input type="checkbox" bind:checked={filters.dryRun} />
				Dry-Run zeigen
			</label>
		</div>
		{#if system.error}
			<Notice level="error">Aktoren nicht benannt: {system.error.detail}</Notice>
		{/if}
	</Card>

	<Card title="Entscheidungen">
		{#if error}
			<Notice level="error">{error.detail}</Notice>
		{/if}
		{#if rows.length > 0}
			{#if loading}
				<p class="muted" role="status">Lädt …</p>
			{/if}
			<div class="rows" class:reloading={loading}>
				<DecisionTable decisions={rows} {names} {wallboxNames} />
			</div>
		{:else if loading}
			<p class="muted">Lädt …</p>
		{:else if !error}
			<p class="muted">Keine Entscheidungen im Zeitraum.</p>
		{/if}
		{#if more && last?.cursor !== undefined}
			<p class="older">
				<button type="button" disabled={loadingMore || loading} onclick={loadOlder}
					>Ältere laden</button
				>
			</p>
		{/if}
	</Card>
</div>

<style>
	.decisions {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem 1.5rem;
		align-items: end;
	}

	.filters label {
		display: grid;
		gap: 0.25rem;
		font-size: 0.875rem;
		color: var(--muted);
		min-width: 0;
	}

	.filters select,
	.filters input[type='search'] {
		max-width: 100%;
	}

	.filters .check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.75rem;
		color: var(--text);
		font-size: 1rem;
	}

	.reloading {
		opacity: 0.55;
	}

	.older {
		margin: 0.75rem 0 0;
	}
</style>
