<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import BacktestTable from '$lib/forecast/BacktestTable.svelte';
	import DailyChart from '$lib/forecast/DailyChart.svelte';
	import FeatureTable from '$lib/forecast/FeatureTable.svelte';
	import ModelCard from '$lib/forecast/ModelCard.svelte';
	import QualityTable from '$lib/forecast/QualityTable.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { hasRole } from '$lib/roles';

	const RETRY_MS = 30_000;
	/** Solange ein Training läuft, wird der Stand in diesem Takt abgefragt. */
	const TRAINING_POLL_MS = 5000;

	// Zeitraum der Güte und des Verlaufs in Tagen; die Abfragen lesen ihn beim Laden
	let days = $state(14);
	const learning = new Resource((o) => api.learningStatus(o), { retryMs: RETRY_MS });
	const quality = new Resource((o) => api.forecastQuality(days, o), { retryMs: RETRY_MS });
	const daily = new Resource((o) => api.forecastQualityDaily(days, o), { retryMs: RETRY_MS });

	onMount(() => {
		const stops = [learning, quality, daily].map((resource) => resource.start());
		return () => stops.forEach((stop) => stop());
	});

	const admin = $derived(hasRole(page.data.user, 'admin'));

	function select(next: number) {
		if (next === days) return;
		days = next;
		void quality.refresh();
		void daily.refresh();
	}

	// Training: abfragen, bis es fertig ist (im Hintergrund, die Sitzung bleibt unverlängert);
	// danach Güte und Verlauf neu laden, weil das Modell sich geändert haben kann
	const running = $derived(learning.data?.training.running ?? false);
	let wasRunning = false;
	$effect(() => {
		if (!running) {
			if (wasRunning) {
				void quality.refresh(true);
				void daily.refresh(true);
			}
			wasRunning = false;
			return;
		}
		wasRunning = true;
		const timer = setInterval(() => void learning.refresh(true), TRAINING_POLL_MS);
		return () => clearInterval(timer);
	});

	let starting = $state(false);
	let message = $state<string | null>(null);

	async function train() {
		starting = true;
		message = null;
		try {
			await api.trainLoadModel();
		} catch (error) {
			message = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
		}
		try {
			await learning.refresh();
		} finally {
			starting = false;
		}
	}
</script>

<div class="forecast">
	<ModelCard
		status={learning.data}
		error={learning.error}
		{admin}
		{message}
		{starting}
		onTrain={train}
	/>
	<QualityTable data={quality.data} error={quality.error} {days} onSelect={select} />
	<DailyChart data={daily.data} error={daily.error} />
	<BacktestTable status={learning.data} />
	<FeatureTable status={learning.data} />
</div>

<style>
	.forecast {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}
</style>
