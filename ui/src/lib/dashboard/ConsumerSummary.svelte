<script lang="ts">
	import type { TreeNode } from '$lib/api/schemas';
	import { consumerRows } from '$lib/consumers/tree';
	import { formatPower, formatRatio } from '$lib/format';

	interface Props {
		tree: TreeNode;
	}

	let { tree }: Props = $props();

	const rows = $derived(consumerRows(tree).filter((row) => row.depth <= 1));

	function share(powerW: number | null): string {
		const house = tree.power_w;
		return house !== null && house > 0 && powerW !== null ? formatRatio(powerW / house) : '';
	}
</script>

<table class="summary">
	<caption class="sr-only">Verbraucher mit Leistung und Anteil an der Hauslast</caption>
	<tbody>
		{#each rows as row (row.key)}
			<tr class={row.kind}>
				<td>
					<span class="swatch" aria-hidden="true" style:background={row.color ?? 'transparent'}
					></span>
					{row.name}
					{#if row.kind === 'other' && row.mismatch}
						<span class="mismatch">(Unterverbraucher messen mehr)</span>
					{/if}
				</td>
				<td class="num">{formatPower(row.powerW)}</td>
				<td class="num muted">{row.kind === 'root' ? '' : share(row.powerW)}</td>
			</tr>
		{/each}
	</tbody>
</table>
<p class="more"><a href="/verbraucher">Alle Verbraucher</a></p>

<style>
	.summary td {
		border-bottom: none;
		padding: 0.25rem 0.375rem;
	}

	.root td {
		font-weight: 600;
		border-bottom: 1px solid var(--border);
	}

	.consumer td:first-child,
	.other td:first-child {
		padding-left: 1.25rem;
	}

	.other td:first-child {
		font-style: italic;
	}

	.swatch {
		display: inline-block;
		width: 0.625rem;
		height: 0.625rem;
		margin-right: 0.375rem;
		border-radius: 2px;
	}

	.mismatch {
		color: var(--warn);
		font-size: 0.875rem;
	}

	.more {
		margin: 0.75rem 0 0;
	}
</style>
