<script lang="ts">
	// Testrahmen: hält den Entwurf als Zustand, damit `bind:draft` wirkt, und gibt ihn dem Test preis
	import { untrack } from 'svelte';
	import HeatingEditor from '$lib/settings/HeatingEditor.svelte';
	import type { HeatingDraft } from '$lib/settings/model';

	interface Props {
		initial: HeatingDraft;
		errors?: Record<string, string>;
		probe: { draft: HeatingDraft | null };
	}

	let { initial, errors = {}, probe }: Props = $props();

	let draft = $state(untrack(() => initial));
	$effect(() => {
		probe.draft = $state.snapshot(draft);
	});
</script>

<HeatingEditor bind:draft {errors} />
