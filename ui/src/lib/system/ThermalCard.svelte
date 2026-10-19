<script lang="ts">
	import type { CoreHealth } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { thermalRows } from './thermal';

	interface Props {
		thermal: CoreHealth['thermal'];
	}

	let { thermal }: Props = $props();

	const devices = $derived(Object.entries(thermal));
</script>

{#if devices.length > 0}
	<Card title="Klimaanlage">
		{#each devices as [name, block] (name)}
			{#if devices.length > 1}
				<h3>{block.label}</h3>
			{/if}
			<p class="status">
				<StatusDot
					state={block.connected ? 'ok' : 'warn'}
					label={block.connected ? 'Werte kommen an' : 'keine Werte'}
				/>
				{block.connected ? 'Werte kommen an' : 'Keine Werte'}
			</p>
			<dl class="facts">
				{#each thermalRows(block) as row (row.label)}
					<dt>{row.label}</dt>
					<dd>{row.value}</dd>
				{/each}
			</dl>
		{/each}
	</Card>
{/if}

<style>
	h3 {
		margin: 0.75rem 0 0.5rem;
		font-size: 0.9375rem;
	}

	.status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0 0 0.75rem;
	}

	.facts {
		margin: 0;
	}
</style>
