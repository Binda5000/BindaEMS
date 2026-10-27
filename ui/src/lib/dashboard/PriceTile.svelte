<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { PricesNow } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { formatCt } from '$lib/format';
	import { cheapestText, priceRangeText, priceSummary } from './prices';

	interface Props {
		data: PricesNow | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	const summary = $derived(data ? priceSummary(data) : null);
	const max = $derived(Math.max(0, ...(summary?.next.map((slot) => slot.ct) ?? [])));
</script>

<div class="tile">
	<span class="label">Strompreis jetzt</span>
	<p class="now">
		<span class="value" data-testid="price-now">{formatCt(summary?.nowCt)}</span>
		{#if summary?.origin === 'fallback'}
			<span class="tag">Ersatzquelle</span>
		{/if}
		{#if summary?.incomplete}
			<span class="tag">Tarif unvollständig</span>
		{/if}
	</p>
	<span class="detail">Einspeisung {formatCt(summary?.feedInCt)}</span>

	{#if summary && summary.next.length > 0}
		<div class="bars" role="img" aria-label={priceRangeText(summary)}>
			{#each summary.next as item (item.start)}
				<span
					class="bar {item.level}"
					style:height="{max > 0 ? Math.max(8, (item.ct / max) * 100) : 8}%"
				></span>
			{/each}
		</div>
		<span class="detail">{cheapestText(summary)}</span>
	{/if}

	{#if error}
		<Notice level="error">{error.detail}</Notice>
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

	.now {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.5rem;
		margin: 0;
	}

	.value {
		font-size: 1.625rem;
		font-weight: 700;
		line-height: 1.2;
	}

	.tag {
		padding: 0.0625rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--warn);
		font-size: 0.75rem;
		font-weight: 600;
	}

	.detail {
		font-size: 0.75rem;
		color: var(--muted);
	}

	.bars {
		display: flex;
		align-items: flex-end;
		gap: 2px;
		height: 1.5rem;
		margin: 0.125rem 0;
	}

	.bar {
		flex: 1;
		min-width: 0;
		border-radius: 2px 2px 0 0;
	}

	.low {
		background: var(--ok);
	}

	.mid {
		background: var(--warn);
	}

	.high {
		background: var(--error);
	}
</style>
