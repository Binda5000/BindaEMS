<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { DASH, formatPower } from '$lib/format';
	import {
		LABEL_MIN_SHARE,
		houseText,
		type Balance,
		type BalanceKind,
		type BalanceSegment,
		type BalanceUnknown
	} from './balance';
	import type { FlowConsumer } from './flow';

	interface Props {
		balance: Balance;
		consumers: FlowConsumer[];
		/** Live-Verbindung veraltet: alle Werte gedämpft */
		stale: boolean;
		error: ApiError | null;
	}

	let { balance, consumers, stale, error }: Props = $props();

	const COLORS: Record<BalanceKind, string> = {
		pv: 'var(--pv)',
		grid: 'var(--grid)',
		battery: 'var(--battery)',
		house: 'var(--house)',
		wallbox: 'var(--wallbox)',
		export: 'var(--grid)'
	};

	const inHouse = $derived(houseText(consumers));
</script>

{#snippet bar(
	id: string,
	title: string,
	total: number | null,
	segments: BalanceSegment[],
	unknown: BalanceUnknown[]
)}
	<div class="side">
		<span class="caption">{title} · {formatPower(total)}</span>
		<div class="bar" data-testid="balance-{id}" aria-hidden="true">
			{#each segments as segment (segment.id)}
				<span
					class="segment {segment.kind}"
					style:flex-grow={segment.share}
					style:background={COLORS[segment.kind]}
				>
					{#if segment.share >= LABEL_MIN_SHARE}{segment.label}{/if}
				</span>
			{/each}
		</div>
		{#if segments.length > 0 || unknown.length > 0}
			<ul class="legend" data-testid="balance-{id}-list">
				{#each segments as segment (segment.id)}
					<li>
						<span class="dot" style:background={COLORS[segment.kind]}></span>
						{segment.label}
						<span class="power">{formatPower(segment.powerW)}</span>
					</li>
				{/each}
				{#each unknown as item (item.id)}
					<li>
						<span class="dot" style:background={COLORS[item.kind]}></span>
						{item.label}
						<span class="power">{DASH}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="empty">{DASH}</p>
		{/if}
	</div>
{/snippet}

<Card title="Woher kommt der Strom – wohin geht er?">
	<div class="body" class:stale>
		{@render bar('sources', 'Quellen', balance.sourcesW, balance.sources, balance.sourcesUnknown)}
		{@render bar('sinks', 'Verbrauch', balance.sinksW, balance.sinks, balance.sinksUnknown)}
		{#if inHouse}
			<p class="house">Im Haus: {inHouse}</p>
		{/if}
	</div>
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	<p class="more"><a href="/verbraucher">Verbraucher im Detail</a></p>
</Card>

<style>
	.body {
		display: grid;
		gap: 0.75rem;
	}

	.stale {
		opacity: 0.6;
	}

	.side {
		display: grid;
		gap: 0.25rem;
	}

	.caption {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.bar {
		display: flex;
		height: 2rem;
		border-radius: 6px;
		overflow: hidden;
		background: var(--surface-2);
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--accent-text);
	}

	.segment {
		flex-basis: 0;
		min-width: 0;
		display: flex;
		align-items: center;
		padding-left: 0.5rem;
		overflow: hidden;
		white-space: nowrap;
	}

	.segment.pv {
		color: var(--on-pv);
	}

	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 0 0.75rem;
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.dot {
		display: inline-block;
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
	}

	.power {
		color: var(--text);
		font-weight: 600;
	}

	.empty,
	.house {
		margin: 0;
		font-size: 0.875rem;
	}

	.empty {
		color: var(--muted);
	}

	.more {
		margin: 0.75rem 0 0;
	}

	.more a {
		display: inline-flex;
		align-items: center;
		min-height: 2.75rem;
	}
</style>
