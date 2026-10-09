<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { PricesNow } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { formatCt, formatTime } from '$lib/format';
	import { priceSummary, type PriceLevel } from './prices';

	interface Props {
		data: PricesNow | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	const LEVELS: Record<PriceLevel, string> = { low: 'günstig', mid: 'mittel', high: 'teuer' };

	const summary = $derived(data ? priceSummary(data) : null);
	const max = $derived(Math.max(0, ...(summary?.next.map((n) => n.ct) ?? [])));
</script>

<div class="price-now">
	<p class="now">
		<span class="value" data-testid="price-now">{formatCt(summary?.nowCt)}</span>
		{#if summary?.origin === 'fallback'}
			<span class="tag">Ersatzquelle</span>
		{/if}
	</p>
	<p class="feed-in muted">Einspeisung {formatCt(summary?.feedInCt)}</p>
	{#if summary?.incomplete}
		<Notice level="warning">Nicht alle Tarifbestandteile eingetragen (als 0 gerechnet)</Notice>
	{/if}
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}

	{#if summary && summary.next.length > 0}
		<ol class="bars" aria-label="Preise der nächsten 3 Stunden">
			{#each summary.next as slot (slot.start)}
				<li class={slot.level} title="{formatTime(slot.start)}: {formatCt(slot.ct)}">
					<span
						class="bar"
						aria-hidden="true"
						style:height="{max > 0 ? Math.max(8, (slot.ct / max) * 100) : 8}%"
					></span>
					<span class="sr-only">
						{formatTime(slot.start)}: {formatCt(slot.ct)}, {LEVELS[slot.level]}
					</span>
				</li>
			{/each}
		</ol>
		<p class="axis muted" aria-hidden="true">
			<span>{formatTime(summary.next[0].start)}</span>
			<span>{formatTime(summary.next[summary.next.length - 1].start)}</span>
		</p>
	{/if}
</div>

<style>
	.price-now {
		display: grid;
		gap: 0.5rem;
	}

	p {
		margin: 0;
	}

	.now {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.value {
		font-size: 2rem;
		font-weight: 700;
		line-height: 1.2;
	}

	.tag {
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--warn);
		font-size: 0.8125rem;
		font-weight: 600;
	}

	.bars {
		list-style: none;
		margin: 0.5rem 0 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(12, 1fr);
		align-items: end;
		gap: 3px;
		height: 4rem;
	}

	.bars li {
		height: 100%;
		display: flex;
		align-items: flex-end;
	}

	.bar {
		display: block;
		width: 100%;
		border-radius: 3px 3px 0 0;
	}

	.low .bar {
		background: var(--ok);
	}

	.mid .bar {
		background: var(--warn);
	}

	.high .bar {
		background: var(--error);
	}

	.axis {
		display: flex;
		justify-content: space-between;
		font-size: 0.8125rem;
	}
</style>
