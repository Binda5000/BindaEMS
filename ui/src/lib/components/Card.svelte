<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title?: string;
		children: Snippet;
	}

	let { title, children }: Props = $props();
</script>

<section class="card">
	{#if title}
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
</style>
