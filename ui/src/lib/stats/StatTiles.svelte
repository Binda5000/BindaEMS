<script lang="ts">
	import type { Tile } from './view';

	interface Props {
		tiles: Tile[];
	}

	let { tiles }: Props = $props();
</script>

<div class="tiles">
	{#each tiles as tile (tile.id)}
		<div class="tile" data-testid="tile">
			<span class="label">{tile.label}</span>
			<strong class="value">{tile.value}</strong>
			{#if tile.subtitle}
				<span class="sub">{tile.subtitle}</span>
			{/if}
		</div>
	{/each}
</div>

<style>
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
		gap: var(--gap);
		min-width: 0;
	}

	.tile {
		display: grid;
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

	.value {
		font-size: 1.5rem;
		line-height: 1.25;
		overflow-wrap: anywhere;
	}

	.sub {
		font-size: 0.8125rem;
		color: var(--muted);
	}
</style>
