<script lang="ts">
	import Segmented from '$lib/components/Segmented.svelte';
	import { PERIODS, isRunning, periodRange, periodTitle, shift, type Period } from './period';

	interface Props {
		period: Period;
		/** gewählter Tag (YYYY-MM-DD) im Zeitraum */
		day: string;
		/** heute in Wien (YYYY-MM-DD) */
		today: string;
		/** einen anderen Zeitraum oder Tag öffnen (über die Adresse) */
		onOpen: (period: Period, day: string) => void;
	}

	let { period, day, today, onOpen }: Props = $props();

	const title = $derived(periodTitle(period, day));
	const range = $derived(periodRange(period, day));
	const running = $derived(isRunning(period, day, today));
</script>

<!-- Zeitraum „Tag | Woche | Monat | Jahr“, darunter Blättern mit Titel und „Heute“ -->
<Segmented
	options={PERIODS}
	value={period}
	label="Zeitraum wählen"
	onSelect={(id) => onOpen(id as Period, day)}
/>
<div class="paging">
	<button
		type="button"
		class="step"
		aria-label="Zurück"
		onclick={() => onOpen(period, shift(period, day, -1))}
	>
		<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
	</button>
	<h2>{title}</h2>
	<button
		type="button"
		class="step"
		aria-label="Weiter"
		disabled={range.last >= today}
		onclick={() => onOpen(period, shift(period, day, 1))}
	>
		<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>
	</button>
	<button type="button" disabled={running} onclick={() => onOpen(period, today)}>Heute</button>
</div>

<style>
	.paging {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.75rem;
	}

	.paging h2 {
		flex: 1 1 0;
		min-width: 0;
		margin: 0;
		font-size: 1.125rem;
		font-weight: 600;
		text-align: center;
		overflow-wrap: anywhere;
	}

	/* Pfeile quadratisch 44 × 44 px; „Heute“ behält seine Breite */
	.paging button {
		flex: none;
	}

	.step {
		display: grid;
		place-items: center;
		width: 2.75rem;
		padding: 0;
	}

	.step svg {
		width: 1.25rem;
		height: 1.25rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 2.5;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
</style>
