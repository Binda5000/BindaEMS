<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { plannedLines } from './view';

	interface Props {
		stats: Stats;
	}

	let { stats }: Props = $props();

	// Abnahme „Ziel-SOC“: die Quote zählt nur machbare Ladungen, die übrigen stehen darunter
	const lines = $derived(plannedLines(stats.planned));
</script>

<Card title="Geplantes Laden">
	{#if lines}
		<p class="rate">{lines.rate}</p>
		<p class="muted">{lines.outside}</p>
		<p class="muted">{lines.changed}</p>
	{:else}
		<p class="muted">Im Zeitraum gab es keine geplante Ladung.</p>
	{/if}
</Card>

<style>
	p {
		margin: 0 0 0.25rem;
		overflow-wrap: anywhere;
	}

	.rate {
		font-weight: 600;
	}
</style>
