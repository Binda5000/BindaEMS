<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { vehicleTrendOption } from '$lib/charts/options';
	import type { Palette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import type { VehicleChoice } from './vehicles';

	interface Props {
		stats: Stats;
		/** gewähltes Auto, `null`: alle */
		vehicle: string | null;
		choices: VehicleChoice[];
		colorOf: (key: string) => string;
		palette: Palette;
		onselect: (key: string | null) => void;
	}

	let { stats, vehicle, choices, colorOf, palette, onselect }: Props = $props();

	const UNITS = [
		{ id: 'kwh', label: 'kWh' },
		{ id: 'eur', label: '€' }
	] as const;

	let unit = $state<'kwh' | 'eur'>('kwh');

	// Tag hat keinen Verlauf (`vehicle_buckets` ist leer); sonst nur mit Ladung des gewählten Autos
	const daily = $derived(stats.period === 'day');
	const loaded = $derived(
		stats.vehicle_buckets.some((bucket) =>
			bucket.vehicles.some((entry) => vehicle === null || entry.key === vehicle)
		)
	);
	const option = $derived(
		!daily && loaded ? vehicleTrendOption(stats, vehicle, unit, colorOf, palette) : null
	);
	const selected = $derived(choices.find((choice) => choice.key === vehicle)?.label ?? 'Alle');
</script>

<Card title="Laden je Auto">
	<div class="controls">
		<div class="switch" role="group" aria-label="Auto wählen">
			{#each choices as choice (choice.key ?? '')}
				<button
					type="button"
					class:active={choice.key === vehicle}
					aria-pressed={choice.key === vehicle}
					onclick={() => onselect(choice.key)}
				>
					{choice.label}
				</button>
			{/each}
		</div>
		<div class="switch" role="group" aria-label="Einheit">
			{#each UNITS as item (item.id)}
				<button
					type="button"
					class:active={unit === item.id}
					aria-pressed={unit === item.id}
					onclick={() => (unit = item.id)}
				>
					{item.label}
				</button>
			{/each}
		</div>
	</div>
	{#if daily}
		<p class="muted">Den Verlauf zeigen Woche, Monat und Jahr.</p>
	{:else if option}
		<EChart {option} label="Laden je Auto {selected}" height="18rem" />
	{:else}
		<p class="muted">Im Zeitraum wurde kein Auto geladen.</p>
	{/if}
</Card>

<style>
	.controls {
		display: grid;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
	}

	.switch {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.switch .active {
		border-color: var(--accent);
		color: var(--accent);
		font-weight: 600;
	}
</style>
