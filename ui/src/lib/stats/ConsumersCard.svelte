<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { consumerRows } from './view';

	interface Props {
		stats: Stats;
	}

	let { stats }: Props = $props();

	const rows = $derived(consumerRows(stats));
</script>

<Card title="Verbraucher">
	{#if rows.length === 0}
		<p class="muted">Keine Verbraucher angelegt.</p>
	{:else}
		<table aria-label="Verbraucher">
			<thead>
				<tr>
					<th scope="col">Verbraucher</th>
					<th scope="col" class="num">Energie</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.id)}
					<tr>
						<td>{row.name}</td>
						<td class="num"><span title={row.title}>{row.value}</span></td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</Card>

<style>
	th {
		font-size: 0.8125rem;
		color: var(--muted);
	}
</style>
