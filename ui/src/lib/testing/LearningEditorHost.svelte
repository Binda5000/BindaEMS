<script lang="ts">
	// Testrahmen: hält den Entwurf als Zustand, damit `bind:draft` wirkt, und gibt ihn dem Test preis
	import { untrack } from 'svelte';
	import LearningEditor from '$lib/settings/LearningEditor.svelte';
	import type { LearningDraft } from '$lib/settings/model';
	import type { SensorCandidates } from '$lib/api/schemas';

	interface Props {
		initial: LearningDraft;
		errors?: Record<string, string>;
		loadCandidates?: () => Promise<SensorCandidates>;
		probe: { draft: LearningDraft | null };
	}

	let { initial, errors = {}, loadCandidates, probe }: Props = $props();

	let draft = $state(untrack(() => initial));
	$effect(() => {
		probe.draft = $state.snapshot(draft);
	});
</script>

<LearningEditor bind:draft {errors} {loadCandidates} />
