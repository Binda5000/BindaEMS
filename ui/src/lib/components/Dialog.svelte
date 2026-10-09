<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		open: boolean;
		title: string;
		children: Snippet;
	}

	let { open = $bindable(false), title, children }: Props = $props();

	const uid = $props.id();
	let dialog = $state<HTMLDialogElement>();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) {
			if (typeof dialog.showModal === 'function') dialog.showModal();
			else dialog.setAttribute('open', '');
		} else if (!open && dialog.open) {
			if (typeof dialog.close === 'function') dialog.close();
			else dialog.removeAttribute('open');
		}
	});
</script>

<dialog bind:this={dialog} aria-labelledby="{uid}-title" onclose={() => (open = false)}>
	<header>
		<h2 id="{uid}-title">{title}</h2>
		<button type="button" class="close" aria-label="Schließen" onclick={() => (open = false)}>
			×
		</button>
	</header>
	{#if open}
		{@render children()}
	{/if}
</dialog>

<style>
	dialog {
		width: min(32rem, calc(100vw - 2rem));
		max-height: calc(100dvh - 2rem);
		padding: var(--gap);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		color: var(--text);
	}

	dialog::backdrop {
		background: rgb(0 0 0 / 0.45);
	}

	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.75rem;
	}

	h2 {
		margin: 0;
		font-size: 1.125rem;
	}

	.close {
		font-size: 1.5rem;
		line-height: 1;
		padding: 0.25rem 0.5rem;
		background: none;
		border: none;
		color: var(--text);
	}
</style>
