<script lang="ts">
	import Card from '$lib/components/Card.svelte';
	import { NBSP, formatNumber, formatTime } from '$lib/format';
	import { blockLabel, type PlanBlock } from './model';

	interface Props {
		blocks: PlanBlock[];
	}

	let { blocks }: Props = $props();
</script>

<Card title="Ablauf">
	<ol class="blocks">
		{#each blocks as block, index (index)}
			<li class={block.intent.toLowerCase()}>
				<p class="head">
					<span class="time">{formatTime(block.start)}–{formatTime(block.end)}</span>
					<strong>{blockLabel(block)}</strong>
					<span class="soc muted"
						>SOC {formatNumber(block.socFrom, 0)} → {formatNumber(block.socTo, 0)}{NBSP}%</span
					>
				</p>
				<p class="text">{block.text}</p>
			</li>
		{/each}
	</ol>
</Card>

<style>
	.blocks {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 0.5rem;
	}

	li {
		padding: 0.25rem 0 0.25rem 0.75rem;
		border-left: 4px solid var(--border);
		min-width: 0;
	}

	li.netzladen {
		border-left-color: var(--battery);
	}

	li.halten {
		border-left-color: var(--pv);
	}

	p {
		margin: 0;
	}

	.head {
		display: flex;
		flex-wrap: wrap;
		gap: 0 0.75rem;
		align-items: baseline;
	}

	.time {
		font-variant-numeric: tabular-nums;
	}

	.text {
		overflow-wrap: anywhere;
	}
</style>
