<script lang="ts">
	import Notice from '$lib/components/Notice.svelte';
	import { noticeSummary, type Notice as NoticeItem } from './notices';

	interface Props {
		notices: NoticeItem[];
	}

	let { notices }: Props = $props();

	/** nur bis zum Neuladen */
	let open = $state(false);
	const summary = $derived(noticeSummary(notices));
</script>

{#if summary}
	<section class="bar {summary.level}" aria-label="Hinweise">
		<div class="line">
			<p class="text">
				<strong>{summary.count}</strong>
				{#if !open}
					<span class="preview">· {summary.preview}</span>
				{/if}
			</p>
			<button
				type="button"
				aria-expanded={open}
				aria-controls="notice-list"
				onclick={() => (open = !open)}
			>
				{open ? 'Ausblenden' : 'Anzeigen'}
			</button>
		</div>
		{#if open}
			<ul id="notice-list">
				{#each notices as notice (notice.text)}
					<li><Notice level={notice.level}>{notice.text}</Notice></li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}

<style>
	.bar {
		border: 1px solid var(--border);
		border-left: 4px solid var(--muted);
		border-radius: var(--radius);
		background: var(--surface);
		padding: 0 0.75rem;
		min-width: 0;
	}

	.error {
		border-left-color: var(--error);
	}

	.warning {
		border-left-color: var(--warn);
	}

	.line {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-height: 2.75rem;
	}

	.text {
		flex: 1;
		min-width: 0;
		margin: 0;
		padding: 0.25rem 0;
		overflow-wrap: anywhere;
	}

	.error .text strong {
		color: var(--error);
	}

	.warning .text strong {
		color: var(--warn);
	}

	.preview {
		color: var(--muted);
	}

	button {
		min-width: 2.75rem;
		min-height: 2.75rem;
		padding: 0 0.75rem;
		border: 0;
		background: none;
		color: var(--text);
		font: inherit;
		font-weight: 600;
		text-decoration: underline;
		cursor: pointer;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0 0 0.75rem;
		display: grid;
		gap: 0.5rem;
	}
</style>
