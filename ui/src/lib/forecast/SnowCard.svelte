<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Forecast, SnowOverride } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { pvSnowOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { forecastForChart } from '$lib/dashboard/prices';
	import { theme } from '$lib/theme.svelte';
	import { OVERRIDE_TEXTS, snowText } from './snow';

	interface Props {
		data: Forecast | undefined;
		error: ApiError | null;
		/** Knöpfe nur ab der Rolle „Bedienen“ */
		operator: boolean;
		/** Einstellung „Schnee erkennen“ (`pv_model.snow_detection`); fehlt, solange nicht geladen */
		detection?: boolean;
		/** Ein Knopf wird gerade gesetzt (zwischen Klick und Antwort) */
		busy?: boolean;
		/** Meldung zum Knopf (Fehler beim Setzen) */
		message?: string | null;
		/** `null`: wieder Automatik */
		onSet: (mode: SnowOverride | null) => void;
		now?: Date;
	}

	let {
		data,
		error,
		operator,
		detection,
		busy = false,
		message = null,
		onSet,
		now = new Date()
	}: Props = $props();

	const snow = $derived(data?.snow ?? null);
	const slots = $derived(forecastForChart(data?.slots ?? [], now));
	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const option = $derived(pvSnowOption(slots, now.toISOString(), palette));
</script>

<Card title="Schnee auf den Modulen">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		{#if snow}
			<p class="state">{snowText(snow, now)}</p>
			{#if snow.override}
				<p class="muted">{OVERRIDE_TEXTS[snow.override]}</p>
			{/if}
		{:else}
			<p class="muted">Noch keine PV-Prognose.</p>
		{/if}
		{#if detection === false}
			<p class="muted">
				Schnee erkennen ist aus (Einstellungen, „PV-Modell“): keine Kürzung, die Knöpfe wirken
				nicht.
			</p>
		{/if}
		{#if operator && snow}
			<div class="buttons">
				<button
					type="button"
					disabled={busy}
					aria-pressed={snow.override === 'covered'}
					onclick={() => onSet('covered')}
				>
					Schnee auf den Modulen
				</button>
				<button
					type="button"
					disabled={busy}
					aria-pressed={snow.override === 'free'}
					onclick={() => onSet('free')}
				>
					Module sind frei
				</button>
				{#if snow.override}
					<button type="button" disabled={busy} onclick={() => onSet(null)}>Automatik</button>
				{/if}
			</div>
		{/if}
		{#if message}
			<Notice level="error">{message}</Notice>
		{/if}
		{#if slots.length > 0}
			<EChart {option} label="PV-Prognose heute und morgen" height="14rem" />
		{/if}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0 0 0.5rem;
	}

	.state {
		font-weight: 600;
	}

	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}
</style>
