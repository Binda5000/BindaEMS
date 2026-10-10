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

	/** Balken unter Maus, Finger oder Tastatur; sein Preis steht darüber */
	let active = $state<number | null>(null);
	const slot = $derived(active === null ? null : (summary?.next[active] ?? null));

	function slotEnd(start: string): string {
		return new Date(Date.parse(start) + 15 * 60_000).toISOString();
	}

	function onKeydown(event: KeyboardEvent) {
		const count = summary?.next.length ?? 0;
		if (count === 0) return;
		const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
		if (step !== 0) {
			event.preventDefault();
			active = Math.min(count - 1, Math.max(0, (active ?? -1) + step));
		} else if (event.key === 'Escape') {
			active = null;
		}
	}
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
		<div class="chart">
			{#if slot && active !== null}
				<p
					class="tip {slot.level}"
					data-testid="price-tip"
					style:--at={(active + 0.5) / summary.next.length}
				>
					<span class="tip-time">{formatTime(slot.start)}–{formatTime(slotEnd(slot.start))}</span>
					<strong>{formatCt(slot.ct)}</strong>
				</p>
			{/if}
			<!-- Balken zeigen ihren Preis bei Maus, Finger und Pfeiltasten; die Werte stehen zusätzlich als Text darin -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
			<ol
				class="bars"
				aria-label="Preise der nächsten 3 Stunden (Pfeiltasten zeigen den Preis je Viertelstunde)"
				tabindex="0"
				onkeydown={onKeydown}
				onblur={() => (active = null)}
				onpointerleave={(event) => {
					if (event.pointerType === 'mouse') active = null;
				}}
			>
				{#each summary.next as item, index (item.start)}
					<li
						class={item.level}
						class:active={active === index}
						class:dimmed={active !== null && active !== index}
						onpointerenter={() => (active = index)}
						onpointerdown={() => (active = index)}
					>
						<span
							class="bar"
							aria-hidden="true"
							style:height="{max > 0 ? Math.max(8, (item.ct / max) * 100) : 8}%"
						></span>
						<span class="sr-only">
							{formatTime(item.start)}: {formatCt(item.ct)}, {LEVELS[item.level]}
						</span>
					</li>
				{/each}
			</ol>
		</div>
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

	.chart {
		position: relative;
		margin-top: 2.25rem; /* Platz für den Preis über dem Balken */
	}

	.tip {
		position: absolute;
		bottom: calc(100% + 0.25rem);
		left: clamp(3.5rem, calc(var(--at) * 100%), calc(100% - 3.5rem));
		transform: translateX(-50%);
		display: flex;
		gap: 0.375rem;
		align-items: baseline;
		margin: 0;
		padding: 0.125rem 0.5rem;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
		box-shadow: 0 2px 6px rgb(0 0 0 / 0.15);
		white-space: nowrap;
		font-size: 0.875rem;
		pointer-events: none;
	}

	.tip-time {
		color: var(--muted);
	}

	.tip.low strong {
		color: var(--ok);
	}

	.tip.mid strong {
		color: var(--warn);
	}

	.tip.high strong {
		color: var(--error);
	}

	.bars {
		list-style: none;
		margin: 0;
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
		cursor: pointer;
		touch-action: manipulation;
	}

	.bars li.dimmed .bar {
		opacity: 0.45;
	}

	.bars li.active .bar {
		outline: 2px solid var(--text);
		outline-offset: 1px;
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
