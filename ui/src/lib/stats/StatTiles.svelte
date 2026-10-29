<script lang="ts">
	import { fitWidth } from './fit';
	import type { Tile } from './view';

	interface Props {
		tiles: Tile[];
	}

	let { tiles }: Props = $props();
</script>

<div class="stat-tiles">
	<div class="tiles">
		{#each tiles as tile (tile.id)}
			<div class="tile" data-testid="tile" data-size={tile.size} style:--chars={tile.value.length}>
				<span class="label">{tile.label}</span>
				<strong class="value" use:fitWidth={tile.value}>{tile.value}</strong>
				{#if tile.subtitle}
					<span class="sub">{tile.subtitle}</span>
				{/if}
			</div>
		{/each}
	</div>
	<p class="note muted">* gegenüber Fixtarif ohne Anlage. „Erlös“ ist der Einspeiseerlös.</p>
</div>

<style>
	/* sechs Spalten: zwei große Kacheln über je drei, drei kleine über je zwei; ab 48rem eine Zeile */
	.tiles {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: var(--gap);
		min-width: 0;
	}

	.tile[data-size='large'] {
		grid-column: span 3;
	}

	.tile[data-size='small'] {
		grid-column: span 2;
	}

	.tile[data-size='large'] .value {
		font-size: 2rem;
	}

	@media (min-width: 48rem) {
		.tiles {
			grid-template-columns: repeat(5, minmax(0, 1fr));
		}

		.tile[data-size] {
			grid-column: auto;
		}

		.tile[data-size='large'] .value {
			font-size: 1.5rem;
		}

		.tile[data-size='small'] {
			--cap: 1.5rem;
		}
	}

	.stat-tiles {
		display: grid;
		gap: 0.5rem;
		min-width: 0;
	}

	.note {
		margin: 0;
		font-size: 0.8125rem;
	}

	.tile {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		align-content: start;
		gap: 0.125rem;
		min-width: 0;
		padding: var(--gap);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}

	.label {
		font-size: 0.875rem;
		color: var(--muted);
	}

	/* Zahl und Einheit bleiben in einer Zeile (geschütztes Leerzeichen vor „€“) */
	.value {
		font-size: 1.5rem;
		line-height: 1.25;
		white-space: nowrap;
	}

	/*
	 * Geldwerte in den kleinen Kacheln: 19 px, bei langen Beträgen („1.234,56 €“) so viel kleiner,
	 * dass der Wert in die Kachel passt (eine Ziffer braucht rund 0,56 em). 100cqi ist die innere
	 * Breite der Kachel.
	 */
	.tile[data-size='small'] {
		container-type: inline-size;
	}

	.tile[data-size='small'] .value {
		font-size: min(var(--cap, 1.1875rem), calc(100cqi / (var(--chars) * 0.56)));
	}

	.sub {
		font-size: 0.8125rem;
		color: var(--muted);
	}
</style>
