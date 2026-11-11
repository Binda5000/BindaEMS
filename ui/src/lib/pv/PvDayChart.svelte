<script lang="ts">
	import type { PvDay, PvSource } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import { formatDay } from '$lib/format';
	import { theme } from '$lib/theme.svelte';
	import { dayNotes, dayOption } from './charts';

	interface Props {
		day: PvDay;
		sources: PvSource[];
		/** jetzt in ms für die Linie „jetzt“; `null`: keine */
		now: number | null;
	}

	let { day, sources, now }: Props = $props();

	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const option = $derived(dayOption(day, sources, palette, now));
	const notes = $derived(dayNotes(day, sources));
</script>

<Card title="Leistung" subtitle="kW · 5-Minuten-Mittel">
	<EChart {option} label="Leistung am {formatDay(day.date)}" height="16rem" />
	{#each notes as note (note)}
		<p class="note">{note}</p>
	{/each}
</Card>

<style>
	.note {
		margin: 0.5rem 0 0;
		color: var(--muted);
		font-size: 0.875rem;
	}
</style>
