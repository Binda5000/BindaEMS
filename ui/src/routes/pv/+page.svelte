<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { PvCandidates, PvDay, PvPeriod, PvSourceInput } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Dialog from '$lib/components/Dialog.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { formatPower } from '$lib/format';
	import PvDayChart from '$lib/pv/PvDayChart.svelte';
	import PvForm from '$lib/pv/PvForm.svelte';
	import PvPeriodChart from '$lib/pv/PvPeriodChart.svelte';
	import PvSystemCard from '$lib/pv/PvSystemCard.svelte';
	import { draftFromSource, emptyDraft } from '$lib/pv/form';
	import { cards, formatKwh, headline, periodLabel, pvHref } from '$lib/pv/tree';
	import { Resource } from '$lib/resource.svelte';
	import { hasRole } from '$lib/roles';
	import PeriodBar from '$lib/stats/PeriodBar.svelte';
	import { isRunning, parseDay, parsePeriod, todayIso, type Period } from '$lib/stats/period';

	const LIVE_MS = 15_000; // Kopf, „Jetzt“ und der laufende Zeitraum (wie „Verbraucher“)
	const CURVE_MS = 60_000; // die Kurve besteht aus 5-Minuten-Mitteln
	const RETRY_MS = 30_000;

	// Wiener Datum, jede Minute neu: „heute“ und der laufende Zeitraum rücken um Mitternacht nach
	let today = $state(todayIso());
	onMount(() => {
		const clock = setInterval(() => (today = todayIso()), 60_000);
		return () => clearInterval(clock);
	});

	// Zeitraum und Tag stehen in der Adresse (?zeitraum=tag|woche|monat|jahr&tag=JJJJ-MM-TT)
	const period = $derived(parsePeriod(page.url.searchParams.get('zeitraum')));
	const day = $derived(parseDay(page.url.searchParams.get('tag'), today));
	const running = $derived(isRunning(period, day, today));

	function open(nextPeriod: Period, nextDay: string) {
		void goto(pvHref(nextPeriod, nextDay), { keepFocus: true, noScroll: true });
	}

	const tree = new Resource((o) => api.pv(o), { intervalMs: LIVE_MS });
	onMount(() => tree.start());

	// Je Zeitraum eine Abfrage: der laufende lädt alle 15 s nach, ein vergangener nur beim Öffnen
	// (nach einem Fehler alle 30 s erneut). Der Tag hat dazu seine Kurve.
	interface Query {
		period: Period;
		running: boolean;
		energy: Resource<PvPeriod>;
		curve: Resource<PvDay> | null;
	}
	let query = $state.raw<Query | null>(null);
	$effect(() => {
		const key = { period, day, running };
		const live = (ms: number) => ({ intervalMs: key.running ? ms : undefined, retryMs: RETRY_MS });
		const next: Query = {
			period: key.period,
			running: key.running,
			energy: new Resource((o) => api.pvPeriod(key.period, key.day, o), live(LIVE_MS)),
			curve:
				key.period === 'day' ? new Resource((o) => api.pvDay(key.day, o), live(CURVE_MS)) : null
		};
		query = next;
		const stops = [next.energy.start(), next.curve?.start()];
		return () => stops.forEach((stop) => stop?.());
	});

	// Beim Blättern bleiben die vorigen Werte gedimmt stehen, bis die neuen da sind
	let shown = $state.raw<{ query: Query; data: PvPeriod } | undefined>(undefined);
	$effect(() => {
		const data = query?.energy.data;
		if (query && data !== undefined) shown = { query, data };
		else if (query?.energy.error) shown = undefined;
	});
	const energy = $derived(query?.energy.data ?? (query?.energy.error ? undefined : shown?.data));
	const stale = $derived(energy !== undefined && energy !== query?.energy.data);
	const label = $derived(
		periodLabel(shown?.query.period ?? period, shown?.query.running ?? running)
	);

	const admin = $derived(hasRole(page.data.user, 'admin'));
	const sources = $derived(tree.data?.sources ?? []);
	const systems = $derived(tree.data ? cards(tree.data, energy) : []);
	const head = $derived(tree.data ? headline(tree.data, energy) : null);
	// „jetzt“ rückt mit jeder neuen Kurve nach
	const now = $derived.by(() => {
		void query?.curve?.data;
		return running && period === 'day' ? Date.now() : null;
	});

	// Formular: Kandidaten erst beim ersten Öffnen laden (InfluxDB kann dauern)
	let candidates = $state.raw<Resource<PvCandidates> | null>(null);
	let stopCandidates: (() => void) | null = null;
	onMount(() => () => stopCandidates?.());

	let formOpen = $state(false);
	let formTitle = $state('');
	let editingId = $state<number | null>(null);
	let draft = $state(emptyDraft([]));
	let deleteOpen = $state(false);
	let message = $state<{ level: 'error' | 'info'; text: string } | null>(null);
	const editing = $derived(sources.find((source) => source.id === editingId));

	function openForm(id: number | null, parentId: number | null = null) {
		const existing = id === null ? undefined : sources.find((source) => source.id === id);
		editingId = existing ? existing.id : null;
		draft = existing ? draftFromSource(existing) : emptyDraft(sources, parentId);
		const string = existing ? existing.parent_id !== null : parentId !== null;
		formTitle = `${string ? 'String' : 'Anlage'} ${existing ? 'ändern' : 'hinzufügen'}`;
		if (candidates === null) {
			const resource = new Resource((o) => api.pvCandidates(o));
			candidates = resource;
			stopCandidates = resource.start();
		}
		message = null;
		formOpen = true;
	}

	async function refresh() {
		await Promise.all([tree.refresh(), query?.energy.refresh(), query?.curve?.refresh()]);
	}

	async function save(input: PvSourceInput) {
		if (editingId === null) await api.createPv(input);
		else await api.updatePv(editingId, input);
		formOpen = false;
		message = {
			level: 'info',
			text: `„${input.name}“ ${editingId === null ? 'angelegt' : 'gespeichert'}.`
		};
		await refresh();
	}

	async function confirmDelete() {
		const target = editing;
		deleteOpen = false;
		formOpen = false;
		if (target === undefined) return;
		try {
			await api.deletePv(target.id);
			message = { level: 'info', text: `„${target.name}“ gelöscht.` };
		} catch (error) {
			message = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
			};
		}
		await refresh();
	}
</script>

<div class="pv">
	<Card>
		<PeriodBar {period} {day} {today} onOpen={open} />
	</Card>

	{#if message}
		<Notice level={message.level}>{message.text}</Notice>
	{/if}
	{#if tree.error}
		<Notice level="error">{tree.error.detail}</Notice>
	{/if}
	{#if query?.energy.error}
		<Notice level="error">{query.energy.error.detail}</Notice>
	{/if}

	{#if tree.data && head}
		<div class="total" class:stale data-testid="pv-total">
			<span class="muted">PV gesamt</span>
			<span><strong>{formatKwh(head.kwh)}</strong> · jetzt {formatPower(head.powerW)}</span>
		</div>
		{#if head.unassignedKwh !== null}
			<p class="unassigned muted" class:stale>
				davon nicht zugeordnet {formatKwh(head.unassignedKwh)}
			</p>
		{/if}

		<div class="cards" class:stale aria-busy={stale}>
			{#each systems as card (card.id)}
				<PvSystemCard
					{card}
					{label}
					{admin}
					onEdit={(id) => openForm(id)}
					onAddString={(id) => openForm(null, id)}
				/>
			{/each}
		</div>

		{#if systems.length === 0}
			<Card>
				<p class="intro">
					Hier erscheint je PV-Anlage eine Karte mit dem Ertrag im Zeitraum, der Leistung jetzt und
					dem Vergleich ihrer Strings.
					{admin ? '' : 'Noch ist keine Anlage angelegt.'}
				</p>
				{#if admin}
					<button type="button" class="add" onclick={() => openForm(null)}>Anlage hinzufügen</button
					>
				{/if}
			</Card>
		{:else}
			{#if period === 'day'}
				{#if query?.curve?.error}
					<Notice level="error">{query.curve.error.detail}</Notice>
				{:else if query?.curve?.data}
					<PvDayChart day={query.curve.data} {sources} {now} />
				{/if}
			{:else if energy}
				<div class="chart" class:stale>
					<PvPeriodChart period={energy} {sources} {today} />
				</div>
			{/if}
			{#if admin}
				<button type="button" class="add" onclick={() => openForm(null)}>
					Weitere Anlage hinzufügen
				</button>
			{/if}
		{/if}
	{:else if !tree.error}
		<p class="muted">Lädt …</p>
	{/if}
</div>

<Dialog bind:open={formOpen} title={formTitle}>
	<PvForm
		bind:draft
		{sources}
		candidates={candidates?.data}
		{editingId}
		onSave={save}
		onCancel={() => (formOpen = false)}
		onDelete={() => (deleteOpen = true)}
	/>
</Dialog>

<Dialog bind:open={deleteOpen} title="{editing?.parent_id === null ? 'Anlage' : 'String'} löschen">
	<p>„{editing?.name}“ wirklich löschen?</p>
	<p class="muted">Mit dem Eintrag gehen auch seine gespeicherten Tage.</p>
	<div class="buttons">
		<button type="button" class="danger" onclick={confirmDelete}>Endgültig löschen</button>
		<button type="button" onclick={() => (deleteOpen = false)}>Abbrechen</button>
	</div>
</Dialog>

<style>
	.pv {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.cards {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.chart {
		min-width: 0;
	}

	.cards:empty {
		display: none;
	}

	.stale {
		opacity: 0.5;
		transition: opacity 0.2s;
	}

	.total {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		justify-content: space-between;
		gap: 0 0.75rem;
		padding: 0 0.125rem;
	}

	.total strong {
		font-size: 1.25rem;
	}

	.unassigned {
		margin: -0.75rem 0 0;
		padding: 0 0.125rem;
		font-size: 0.875rem;
		text-align: right;
	}

	.intro {
		margin: 0 0 0.75rem;
	}

	.add {
		width: 100%;
		min-height: 3.5rem;
		border: 1px dashed var(--muted);
		border-radius: var(--radius);
		background: transparent;
		color: var(--accent);
		font-weight: 600;
	}

	.buttons {
		display: flex;
		gap: 0.5rem;
	}

	.danger {
		border-color: var(--error);
		background: var(--error);
		color: #fff;
	}
</style>
