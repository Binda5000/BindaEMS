<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import BacktestTable from '$lib/forecast/BacktestTable.svelte';
	import DailyChart from '$lib/forecast/DailyChart.svelte';
	import DrivingCard from '$lib/forecast/DrivingCard.svelte';
	import FeatureTable from '$lib/forecast/FeatureTable.svelte';
	import ModelCard from '$lib/forecast/ModelCard.svelte';
	import PresenceChart from '$lib/forecast/PresenceChart.svelte';
	import QualityTable from '$lib/forecast/QualityTable.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { hasRole } from '$lib/roles';

	const RETRY_MS = 30_000;
	/** Solange ein Training läuft, wird der Stand in diesem Takt abgefragt. */
	const TRAINING_POLL_MS = 5000;

	/** Die Abfrage im Takt ruht, solange der Tab verborgen ist (wie bei `Resource`, `lib/activity.ts`). */
	function poll(refresh: () => void): () => void {
		const timer = setInterval(() => {
			if (document.visibilityState !== 'hidden') refresh();
		}, TRAINING_POLL_MS);
		return () => clearInterval(timer);
	}

	// Zeitraum der Güte und des Verlaufs in Tagen; die Abfragen lesen ihn beim Laden
	let days = $state(14);
	const learning = new Resource((o) => api.learningStatus(o), { retryMs: RETRY_MS });
	const quality = new Resource((o) => api.forecastQuality(days, o), { retryMs: RETRY_MS });
	const daily = new Resource((o) => api.forecastQualityDaily(days, o), { retryMs: RETRY_MS });
	const driving = new Resource((o) => api.drivingStatus(o), { retryMs: RETRY_MS });
	// Die Entität der Anwesenheit nennt nur die Quelle im Kopf eines Autos: eine Zugabe, ohne sie
	// steht dort „Home Assistant“.
	const settings = new Resource((o) => api.settings(o), { retryMs: RETRY_MS });

	onMount(() => {
		const stops = [learning, quality, daily, driving, settings].map((resource) => resource.start());
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
		return poll(() => void learning.refresh(true));
	});

	let starting = $state(false);
	let message = $state<string | null>(null);
	let messageLevel = $state<'error' | 'info'>('error');

	// Fahrverhalten: Solange eine Berechnung läuft, wird der Stand abgefragt, bis sie fertig ist
	const drivingRunning = $derived(driving.data?.running ?? false);
	$effect(() => {
		if (!drivingRunning) return;
		return poll(() => void driving.refresh(true));
	});

	let recomputing = $state(false);
	let recomputeMessage = $state<string | null>(null);
	const presenceEntities = $derived(
		settings.data?.settings.learning.driving?.presence_entities ?? {}
	);

	async function recompute() {
		recomputing = true;
		recomputeMessage = null;
		try {
			await api.recomputeDriving();
		} catch (error) {
			// 409: Es läuft schon eine Berechnung. Das ist kein Fehler; der Stand zeigt den Lauf.
			if (!(error instanceof ApiError && error.status === 409)) {
				recomputeMessage = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
			}
		}
		try {
			await driving.refresh();
		} finally {
			recomputing = false;
		}
	}

	async function train() {
		starting = true;
		message = null;
		try {
			await api.trainLoadModel();
		} catch (error) {
			// 409: Es läuft schon ein Training. Das ist kein Fehler, sondern ein Hinweis.
			messageLevel = error instanceof ApiError && error.status === 409 ? 'info' : 'error';
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
		{messageLevel}
		{starting}
		onTrain={train}
	/>
	<QualityTable data={quality.data} error={quality.error} {days} onSelect={select} />
	<DailyChart data={daily.data} error={daily.error} />
	<BacktestTable status={learning.data} />
	<FeatureTable status={learning.data} />
	<DrivingCard
		data={driving.data}
		error={driving.error}
		{admin}
		starting={recomputing}
		message={recomputeMessage}
		{presenceEntities}
		onRecompute={recompute}
	/>
	<PresenceChart data={driving.data} error={driving.error} />
</div>

<style>
	.forecast {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}
</style>
