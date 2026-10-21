<script lang="ts">
	// Testrahmen: hält den Entwurf als Zustand, damit `bind:draft` wirkt, und gibt ihn dem Test preis
	import { untrack } from 'svelte';
	import DrivingEditor from '$lib/settings/DrivingEditor.svelte';
	import type { DrivingDraft } from '$lib/settings/model';

	interface Props {
		initial: DrivingDraft;
		errors?: Record<string, string>;
		vehicles?: Record<string, { name: string }>;
		probe: { draft: DrivingDraft | null };
	}

	let { initial, errors = {}, vehicles = {}, probe }: Props = $props();

	let draft = $state(untrack(() => initial));
	$effect(() => {
		probe.draft = $state.snapshot(draft);
	});
</script>

<DrivingEditor bind:draft {errors} {vehicles} />
