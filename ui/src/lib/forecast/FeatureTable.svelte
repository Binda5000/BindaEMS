<script lang="ts">
	import type { LearningStatus } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { featureLabel, featureRows } from './view';

	interface Props {
		/** `undefined`: der Stand ist noch nicht da */
		status: LearningStatus | undefined;
	}

	/** So viele Merkmale stehen zunächst da; der Rest hinter dem Knopf */
	const SHOWN = 5;

	let { status }: Props = $props();

	let all = $state(false);
	const rows = $derived(status?.model ? featureRows(status.model.features) : []);
	const visible = $derived(all ? rows : rows.slice(0, SHOWN));
	// die Balken zeigen den Anteil im Verhältnis zum größten
	const top = $derived(Math.max(...rows.map((row) => row.share), 1));
	const unused = $derived((status?.sensors ?? []).filter((sensor) => !sensor.in_model));
</script>

<Card title="Was das Modell nutzt">
	{#if rows.length > 0}
		<ul class="features">
			{#each visible as row (row.name)}
				<li>
					<span class="name">{row.label}</span>
					<span class="track" aria-hidden="true">
						<span class="bar" style:width="{(Math.min(row.share, 100) / top) * 100}%"></span>
					</span>
					<span class="share">{row.text}</span>
				</li>
			{/each}
		</ul>
		{#if rows.length > SHOWN}
			<button type="button" class="more" aria-expanded={all} onclick={() => (all = !all)}>
				{all ? 'weniger zeigen' : `alle ${rows.length} Merkmale zeigen`}
			</button>
		{/if}
	{:else if status}
		<p class="muted">Noch kein Modell trainiert.</p>
	{:else}
		<p class="muted">Lädt …</p>
	{/if}
	{#if unused.length > 0}
		<ul class="unused muted">
			{#each unused as sensor (sensor.entity_id)}
				<li>{featureLabel(sensor.entity_id)}: nicht im Modell</li>
			{/each}
		</ul>
	{/if}
</Card>

<style>
	p {
		margin: 0;
	}

	.features {
		display: grid;
		gap: 0.5rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.features li {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 3.5rem;
		align-items: center;
		gap: 0.75rem;
	}

	.name {
		overflow-wrap: anywhere;
	}

	.track {
		height: 0.5rem;
		border-radius: 0.25rem;
		background: var(--surface-2);
	}

	.bar {
		display: block;
		height: 100%;
		border-radius: 0.25rem;
		background: var(--accent);
	}

	.share {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.more {
		margin-top: 0.75rem;
	}

	.unused {
		margin: 0.75rem 0 0;
		padding-left: 1.25rem;
		font-size: 0.875rem;
	}
</style>
