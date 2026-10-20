<script lang="ts">
	import type { LearningStatus } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { featureRows } from './view';

	interface Props {
		/** `undefined`: der Stand ist noch nicht da */
		status: LearningStatus | undefined;
	}

	let { status }: Props = $props();

	const rows = $derived(status?.model ? featureRows(status.model.features) : []);
	const unused = $derived((status?.sensors ?? []).filter((sensor) => !sensor.in_model));
</script>

<Card title="Merkmale">
	{#if rows.length > 0}
		<div class="scroll">
			<table>
				<thead>
					<tr>
						<th scope="col">Merkmal</th>
						<th scope="col" class="num">Anteil</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.name)}
						<tr>
							<td>{row.label}</td>
							<td class="num">
								<span class="bar" aria-hidden="true" style:width="{Math.min(row.share, 100) * 0.6}%"
								></span>
								{row.text}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else if status}
		<p class="muted">Noch kein Modell trainiert.</p>
	{:else}
		<p class="muted">Lädt …</p>
	{/if}
	{#if unused.length > 0}
		<ul class="unused muted">
			{#each unused as sensor (sensor.entity_id)}
				<li>{sensor.entity_id}: nicht im Modell</li>
			{/each}
		</ul>
	{/if}
</Card>

<style>
	p {
		margin: 0;
	}

	.bar {
		display: inline-block;
		height: 0.5rem;
		margin-right: 0.5rem;
		border-radius: 0.25rem;
		background: var(--accent);
		vertical-align: baseline;
	}

	.unused {
		margin: 0.75rem 0 0;
		padding-left: 1.25rem;
		font-size: 0.875rem;
	}
</style>
