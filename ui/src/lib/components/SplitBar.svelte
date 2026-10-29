<script lang="ts">
	interface Props {
		/** Energie in kWh nach Herkunft */
		pv: number;
		battery: number;
		grid: number;
	}

	let { pv, battery, grid }: Props = $props();

	const total = $derived(pv + battery + grid);
	const parts = $derived([
		{ key: 'pv', label: 'PV', kwh: pv },
		{ key: 'battery', label: 'Akku', kwh: battery },
		{ key: 'grid', label: 'Netz', kwh: grid }
	]);
	const share = (kwh: number) => (total > 0 ? (kwh / total) * 100 : 0);
	const label = $derived(
		total > 0
			? parts.map((part) => `${part.label} ${Math.round(share(part.kwh))} %`).join(', ')
			: 'keine Energie'
	);
</script>

<span class="bar" role="img" aria-label={label}>
	{#if total > 0}
		{#each parts as part (part.key)}
			<span data-part={part.key} style:width="{share(part.kwh).toFixed(2)}%"></span>
		{/each}
	{/if}
</span>

<style>
	.bar {
		display: flex;
		width: 100%;
		min-width: 5rem;
		height: 8px;
		overflow: hidden;
		border-radius: 4px;
		background: var(--border);
	}

	[data-part='pv'] {
		background: var(--pv);
	}

	[data-part='battery'] {
		background: var(--battery);
	}

	[data-part='grid'] {
		background: var(--grid);
	}
</style>
