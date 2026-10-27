<script lang="ts">
	import Sparkline from './Sparkline.svelte';

	interface Props {
		label: string;
		value: string;
		/** Tageswert o. Ä. unter der Mini-Linie */
		detail?: string;
		/** CSS-Variable der Kachel, etwa `var(--pv)` */
		color: string;
		points?: [number, number][];
		stale?: boolean;
		/** `data-testid` & Co. kommen vom Aufrufer */
		[key: `data-${string}`]: string | undefined;
	}

	let { label, value, detail, color, points, stale = false, ...rest }: Props = $props();
</script>

<div class="tile" class:stale {...rest}>
	<span class="label">{label}</span>
	<span class="value">{value}</span>
	{#if points}
		<Sparkline {points} {color} />
	{/if}
	{#if detail}
		<span class="detail">{detail}</span>
	{/if}
</div>

<style>
	.tile {
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
		min-width: 0;
		padding: 0.875rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}

	.label {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.value {
		font-size: 1.625rem;
		font-weight: 700;
		line-height: 1.2;
		overflow-wrap: anywhere;
	}

	.detail {
		font-size: 0.75rem;
		color: var(--muted);
	}

	.stale {
		opacity: 0.6;
	}
</style>
