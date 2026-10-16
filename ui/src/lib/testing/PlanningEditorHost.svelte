<script lang="ts">
	// Testrahmen: hält den Entwurf als Zustand, damit `bind:draft` wirkt, und gibt ihn dem Test preis
	import { untrack } from 'svelte';
	import PlanningEditor from '$lib/settings/PlanningEditor.svelte';
	import type { PlanningDraft } from '$lib/settings/model';

	interface Props {
		initial: PlanningDraft;
		errors?: Record<string, string>;
		probe: { draft: PlanningDraft | null };
	}

	let { initial, errors = {}, probe }: Props = $props();

	let draft = $state(untrack(() => initial));
	$effect(() => {
		probe.draft = $state.snapshot(draft);
	});
</script>

<PlanningEditor bind:draft {errors} />
