<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { TreeNode } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { consumerRows } from '$lib/consumers/tree';
	import { formatPower, formatRatio } from '$lib/format';

	interface Props {
		tree: TreeNode | undefined;
		/** Fehler der letzten Ladung; vorhandene Werte gelten dann als veraltet */
		error: ApiError | null;
	}

	let { tree, error }: Props = $props();

	const rows = $derived(tree ? consumerRows(tree).filter((row) => row.depth <= 1) : []);

	function share(powerW: number | null): string {
		const house = tree?.power_w ?? null;
		return house !== null && house > 0 && powerW !== null ? formatRatio(powerW / house) : '';
	}
</script>

{#if error}
	<Notice level="error">{error.detail}</Notice>
{/if}
{#if !tree}
	{#if !error}
		<p class="muted">Lädt …</p>
	{/if}
{:else}
	{#if error}
		<p class="stale">veraltet</p>
	{/if}
	<table class="summary" data-stale={error !== null}>
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
{/if}
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

	.summary[data-stale='true'] {
		opacity: 0.55;
	}

	.stale {
		margin: 0.5rem 0 0;
		color: var(--warn);
		font-size: 0.875rem;
	}

	.more {
		margin: 0.75rem 0 0;
	}
</style>
