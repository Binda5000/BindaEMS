<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { DASH, formatSoc } from '$lib/format';
	import { ringArc, socNote, type VehicleCard } from './vehicles';

	interface Props {
		/** `undefined`: Fahrzeuge noch nicht geladen */
		cards: VehicleCard[] | undefined;
		/** Live-Verbindung veraltet: alle Werte gedämpft */
		stale: boolean;
		/** Fahrzeuge (Grenzen der Anlage) nicht geladen */
		error: ApiError | null;
	}

	let { cards, stale, error }: Props = $props();

	const CIRCUMFERENCE = 2 * Math.PI * 25;
</script>

<Card title="Autos">
	{#if cards === undefined}
		{#if !error}
			<p class="muted">Lädt …</p>
		{/if}
	{:else if cards.length === 0}
		{#if !error}
			<p class="muted">Keine Autos eingetragen.</p>
		{/if}
	{:else}
		<ul class="cards" class:stale>
			{#each cards as card (card.id)}
				{@const note = socNote(card)}
				<li class="car" class:dim={card.socQuality === 'stale'}>
					{#if !card.guest}
						<svg
							class="ring"
							viewBox="0 0 60 60"
							role="img"
							aria-label="{card.name} {card.socPct === null
								? 'kein Ladestand'
								: formatSoc(card.socPct)}"
						>
							<circle cx="30" cy="30" r="25" class="track" />
							<circle
								cx="30"
								cy="30"
								r="25"
								class="arc"
								stroke-dasharray="{ringArc(card.socPct)} {CIRCUMFERENCE}"
								transform="rotate(-90 30 30)"
							/>
							<text x="30" y="35" text-anchor="middle">
								{card.socPct === null ? DASH : Math.round(card.socPct)}
							</text>
						</svg>
					{/if}
					<div class="text">
						<strong>{card.name}</strong>
						<span>{card.status}</span>
						{#if card.mode}<span>{card.mode}</span>{/if}
						{#if note}<span class="note">{note}</span>{/if}
					</div>
				</li>
			{/each}
		</ul>
	{/if}
	{#if error}
		<Notice level="error">Fahrzeuge nicht geladen: {error.detail}</Notice>
	{/if}
	<p class="more"><a href="/laden">Zum Laden</a></p>
</Card>

<style>
	.cards {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
		gap: 0.75rem;
	}

	.stale {
		opacity: 0.6;
	}

	.car {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-width: 0;
		padding: 0.75rem;
		border: 1px solid var(--border);
		border-radius: 10px;
	}

	.ring {
		flex: none;
		width: 3.5rem;
		height: 3.5rem;
	}

	.dim .ring {
		opacity: 0.6;
	}

	.track {
		fill: none;
		stroke: var(--surface-2);
		stroke-width: 7;
	}

	.arc {
		fill: none;
		stroke: var(--battery);
		stroke-width: 7;
	}

	text {
		fill: var(--text);
		font-size: 14px;
		font-weight: 700;
	}

	.text {
		display: flex;
		flex-direction: column;
		min-width: 0;
		font-size: 0.8125rem;
		color: var(--muted);
		overflow-wrap: anywhere;
	}

	.text strong {
		font-size: 0.9375rem;
		color: var(--text);
	}

	.note {
		color: var(--warn);
	}

	p {
		margin: 0;
	}

	.more {
		margin-top: 0.75rem;
	}

	.more a {
		display: inline-flex;
		align-items: center;
		min-height: 2.75rem;
	}
</style>
