<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { QualityDaily } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { qualityDailyOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { theme } from '$lib/theme.svelte';
	import { dailySeries } from './view';

	interface Props {
		data: QualityDaily | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	const series = $derived(dailySeries(data?.rows ?? []));
	const empty = $derived(series.profile.length === 0 && series.model.length === 0);
	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const option = $derived(qualityDailyOption(series, palette));
</script>

<Card title="Fehler je Tag">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		{#if empty}
			<p class="muted">Noch keine Werte im Zeitraum.</p>
		{:else}
			<EChart {option} label="Fehler je Tag, Profil und Modell" height="18rem" />
		{/if}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0;
	}
</style>
