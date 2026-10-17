<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { Plan } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { planPriceOption, planSocOption } from '$lib/plan/chart';
	import { planBlocks } from '$lib/plan/model';
	import PlanBlocks from '$lib/plan/PlanBlocks.svelte';
	import PlanHeader from '$lib/plan/PlanHeader.svelte';
	import PlanReview from '$lib/plan/PlanReview.svelte';
	import { SOC_SERIES } from '$lib/plan/review';
	import { Resource } from '$lib/resource.svelte';
	import { canOperate } from '$lib/roles';
	import { theme } from '$lib/theme.svelte';
	import { localDayRange, todayVienna } from '$lib/time';

	/** So oft fragt die Seite nach „Neu planen“, und so lange höchstens */
	const POLL_MS = 2000;
	const POLL_MAX_MS = 40_000;

	const REVIEW_MS = 300_000;

	const plan = new Resource((o) => api.plan(o), { intervalMs: 60_000 });
	const review = new Resource((o) => api.planReview(o), { intervalMs: REVIEW_MS });
	// der Ist-SOC gehört zum Tag der Antwort „Plan gegen Ist“ (den bestimmt der Server)
	let socDay = todayVienna();
	const soc = new Resource(
		(o) => {
			socDay = review.data?.day ?? todayVienna();
			return api.history([SOC_SERIES], localDayRange(socDay), o);
		},
		{ intervalMs: REVIEW_MS }
	);

	let nowIso = $state(new Date().toISOString()); // „jetzt“ in den Diagrammen, jede Minute weiter
	let replanning = $state(false);
	let switching = $state(false);
	let actionError = $state<string | null>(null);
	let stopped = false;
	let timer: ReturnType<typeof setTimeout> | undefined;

	onMount(() => {
		const stops = [plan.start(), review.start(), soc.start()];
		const clock = setInterval(() => (nowIso = new Date().toISOString()), 60_000);
		return () => {
			stopped = true;
			for (const stop of stops) stop();
			clearInterval(clock);
			clearTimeout(timer);
		};
	});

	const operate = $derived(canOperate(page.data.user));
	const current = $derived(plan.data?.plan ?? null);
	const blocks = $derived(planBlocks(current?.slots ?? []));

	// Farben neu lesen, wenn hell/dunkel wechselt
	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const priceOption = $derived(current ? planPriceOption(current, nowIso, palette) : null);
	const socOption = $derived(current ? planSocOption(current, nowIso, palette) : null);

	// kommt die Antwort mit einem anderen Tag als dem schon geladenen, den SOC dieses Tages holen
	$effect(() => {
		const day = review.data?.day;
		if (day !== undefined && day !== socDay) void soc.refresh();
	});

	function failure(error: unknown): string {
		return error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
	}

	/** Kennzeichen eines Plans: ein neuer Lauf ändert Nummer oder Zeit */
	function signature(value: Plan | null | undefined): string {
		return value ? `${value.id}|${value.created_at}` : '';
	}

	/** Fragt nach einem Auslöser alle 2 s, bis der Plan wechselt (höchstens 40 s); zeigt „Plant …“ */
	async function waitForNewPlan(before: string): Promise<void> {
		replanning = true;
		try {
			if (signature(plan.data?.plan) !== before) return; // der neue Plan ist schon da
			const deadline = Date.now() + POLL_MAX_MS;
			while (!stopped && Date.now() < deadline) {
				await new Promise<void>((resolve) => (timer = setTimeout(resolve, POLL_MS)));
				if (stopped) return;
				await plan.refresh();
				if (plan.error === null && signature(plan.data?.plan) !== before) break;
			}
		} finally {
			replanning = false;
		}
	}

	async function setGridCharge(on: boolean): Promise<void> {
		if (replanning) return;
		switching = true;
		replanning = true; // „Neu planen“ bleibt für den ganzen Ablauf des Schalters gesperrt
		actionError = null;
		const before = signature(current);
		try {
			await api.setGridCharge(on);
			await plan.refresh();
			// die geänderte Einstellung löst eine Neuplanung aus: deren Plan abwarten
			await waitForNewPlan(before);
		} catch (error) {
			actionError = failure(error);
		} finally {
			switching = false;
			replanning = false;
		}
	}

	async function replan(): Promise<void> {
		if (replanning) return;
		replanning = true;
		actionError = null;
		const before = signature(current);
		try {
			await api.replan();
			await waitForNewPlan(before);
		} catch (error) {
			actionError = failure(error);
		} finally {
			replanning = false;
		}
	}
</script>

<div class="plan">
	{#if plan.error}
		<Notice level="error">{plan.error.detail}</Notice>
	{/if}
	{#if actionError}
		<Notice level="error">{actionError}</Notice>
	{/if}

	{#if plan.data}
		<PlanHeader
			data={plan.data}
			canOperate={operate}
			{replanning}
			{switching}
			onGridCharge={setGridCharge}
			onReplan={replan}
		/>
		{#if current && priceOption && socOption}
			<div class="charts">
				<Card title="Preise und Prognosen">
					<EChart option={priceOption} label="Preise und Prognosen" height="16rem" />
				</Card>
				<Card title="Akku">
					<EChart option={socOption} label="Akku" height="16rem" />
				</Card>
			</div>
			<PlanBlocks {blocks} />
		{/if}
	{:else if !plan.error}
		<p class="muted">Lädt …</p>
	{/if}

	<PlanReview review={review.data} soc={soc.data} error={review.error} {palette} />
</div>

<style>
	.plan {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.charts {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr));
		gap: var(--gap);
		min-width: 0;
	}
</style>
