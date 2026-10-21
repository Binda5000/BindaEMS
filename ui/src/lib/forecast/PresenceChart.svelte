<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { DrivingStatus } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { presenceOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { theme } from '$lib/theme.svelte';

	interface Props {
		data: DrivingStatus | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	const TITLE = 'Wahrscheinlichkeit angesteckt, nächste 48 Stunden';

	const empty = $derived((data?.vehicles ?? []).every((car) => car.presence.length === 0));
	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const option = $derived(presenceOption(data?.vehicles ?? [], palette));
</script>

<Card title="Anwesenheit">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		{#if empty}
			<p class="muted">Noch keine Daten.</p>
		{:else}
			<p class="caption">{TITLE}</p>
			<EChart {option} label={TITLE} height="16rem" />
		{/if}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0;
	}

	.caption {
		margin-bottom: 0.5rem;
		font-weight: 600;
	}
</style>
