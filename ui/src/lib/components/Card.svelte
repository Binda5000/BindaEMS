<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title?: string;
		/** gedämpfte Zeile unter dem Titel */
		subtitle?: string;
		/** Etikett rechts neben dem Titel */
		aside?: Snippet;
		children: Snippet;
	}

	let { title, subtitle, aside, children }: Props = $props();
</script>

<section class="card">
	{#if title && (subtitle || aside)}
		<header>
			<div class="titles">
				<h2>{title}</h2>
				{#if subtitle}<p class="subtitle">{subtitle}</p>{/if}
			</div>
			{#if aside}{@render aside()}{/if}
		</header>
	{:else if title}
		<h2>{title}</h2>
	{/if}
	{@render children()}
</section>

<style>
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: var(--gap);
		min-width: 0;
	}

	h2 {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		font-weight: 600;
	}

	header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.5rem 0.75rem;
		margin-bottom: 0.75rem;
	}

	.titles {
		min-width: 0;
	}

	header h2 {
		margin: 0;
	}

	.subtitle {
		margin: 0.125rem 0 0;
		color: var(--muted);
		font-size: 0.875rem;
		overflow-wrap: anywhere;
	}
</style>
