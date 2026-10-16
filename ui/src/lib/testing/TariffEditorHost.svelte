<script lang="ts">
	// Testrahmen: hält den Entwurf als Zustand, damit `bind:draft` wirkt, und gibt ihn dem Test preis
	import { untrack } from 'svelte';
	import TariffEditor from '$lib/settings/TariffEditor.svelte';
	import type { TariffDraft } from '$lib/settings/model';

	interface Props {
		initial: TariffDraft;
		errors?: Record<string, string>;
		probe: { draft: TariffDraft | null; structure: number };
	}

	let { initial, errors = {}, probe }: Props = $props();

	let draft = $state(untrack(() => initial));
	$effect(() => {
		probe.draft = $state.snapshot(draft);
	});
</script>

<TariffEditor bind:draft {errors} onStructure={() => (probe.structure += 1)} />
