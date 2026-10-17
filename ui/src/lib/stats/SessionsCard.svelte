<script lang="ts">
	import type { ChargingSession } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { sessionRows } from './view';

	interface Props {
		/** `undefined` bis zur ersten Antwort */
		sessions: ChargingSession[] | undefined;
		/** Meldung, wenn die Abfrage scheiterte */
		error?: string;
	}

	let { sessions, error }: Props = $props();

	const rows = $derived(sessions ? sessionRows(sessions) : []);
</script>

<Card title="Ladesessions">
	{#if !sessions}
		{#if error}
			<Notice level="error">{error}</Notice>
		{:else}
			<p class="muted">Lädt …</p>
		{/if}
	{:else if rows.length === 0}
		<p class="muted">Keine Ladesessions im Zeitraum.</p>
	{:else}
		<div class="scroll">
			<table aria-label="Ladesessions">
				<thead>
					<tr>
						<th scope="col">Beginn</th>
						<th scope="col">Ende</th>
						<th scope="col">Wallbox</th>
						<th scope="col">Auto</th>
						<th scope="col" class="num">Energie</th>
						<th scope="col" class="num">PV / Akku / Netz</th>
						<th scope="col" class="num">Kosten</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.id)}
						<tr>
							<td>{row.start}</td>
							<td>{row.end}</td>
							<td>{row.wallbox}</td>
							<td>{row.vehicle}</td>
							<td class="num">{row.energy}</td>
							<td class="num">{row.split}</td>
							<td class="num">{row.cost}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if rows.length >= 200}
			<p class="muted limit">Angezeigt werden die 200 neuesten Sessions.</p>
		{/if}
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

	.limit {
		margin: 0.5rem 0 0;
		font-size: 0.875rem;
	}
</style>
