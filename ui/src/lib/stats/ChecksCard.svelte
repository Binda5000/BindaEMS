<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { checkNotice, checkRows } from './view';

	interface Props {
		stats: Stats;
		/** Anzeigenamen der Wallboxes nach Schlüssel; ohne Eintrag gilt der Schlüssel */
		wallboxNames?: Record<string, string>;
	}

	let { stats, wallboxNames = {} }: Props = $props();

	const rows = $derived(checkRows(stats, wallboxNames));
	const notice = $derived(checkNotice(stats));
	const checked = $derived(stats.checks.days > 0);
</script>

<Card title="Abgleich mit den Zählern">
	{#if checked && rows.length > 0}
		<div class="scroll">
			<table aria-label="Abgleich mit den Zählern">
				<thead>
					<tr>
						<th scope="col">Größe</th>
						<th scope="col" class="num">Abrechnung</th>
						<th scope="col" class="num">Zähler</th>
						<th scope="col" class="num">Abweichung</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.signal)}
						<tr>
							<td>{row.label}</td>
							<td class="num">{row.ledger}</td>
							<td class="num">{row.counter}</td>
							<td class="num">{row.deviation}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
	{#if notice}
		<p class="notice" class:muted={!checked}>{notice}</p>
	{/if}
</Card>

<style>
	table {
		min-width: max-content;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
		white-space: nowrap;
	}

	td {
		white-space: nowrap;
	}

	.notice {
		margin: 0.75rem 0 0;
	}

	.notice:first-child {
		margin-top: 0;
	}
</style>
