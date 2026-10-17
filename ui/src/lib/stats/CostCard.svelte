<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { costRows, savingsRows } from './view';

	interface Props {
		stats: Stats;
	}

	let { stats }: Props = $props();

	const rows = $derived(costRows(stats));
	const savings = $derived(savingsRows(stats));
</script>

<Card title="Kosten">
	<table>
		<tbody>
			{#each rows as row (row.id)}
				<tr class:total={row.strong}>
					<td>{row.label}</td>
					<td class="num">{row.value}</td>
				</tr>
			{/each}
		</tbody>
	</table>
	<table class="savings">
		<tbody>
			{#each savings as row (row.id)}
				<tr>
					<td>{row.label}</td>
					<td class="num">{row.value}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</Card>

<style>
	table {
		width: 100%;
	}

	td {
		padding: 0.375rem 0;
	}

	.num {
		text-align: right;
		white-space: nowrap;
	}

	.total td {
		font-weight: 600;
		border-top: 1px solid var(--border);
	}

	.savings {
		margin-top: 0.75rem;
		padding-top: 0.5rem;
		border-top: 1px solid var(--border);
		color: var(--muted);
		font-size: 0.9375rem;
	}
</style>
