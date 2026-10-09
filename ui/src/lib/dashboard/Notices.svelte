<script lang="ts">
	import Notice from '$lib/components/Notice.svelte';
	import type { Notice as NoticeItem } from './notices';

	interface Props {
		notices: NoticeItem[];
	}

	let { notices }: Props = $props();
</script>

{#if notices.length === 0}
	<p class="muted">Keine Hinweise</p>
{:else}
	<ul class="notices">
		{#each notices as notice (notice.text)}
			<li><Notice level={notice.level}>{notice.text}</Notice></li>
		{/each}
	</ul>
{/if}

<style>
	.notices {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.5rem;
	}

	p {
		margin: 0;
	}
</style>
