<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import type { SettingsCurrent } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { EXPORT_SETTINGS_URL } from '$lib/api/endpoints';

	interface Props {
		importSettings: (yaml: string) => Promise<SettingsCurrent>;
		onImported: (result: SettingsCurrent) => void;
	}

	let { importSettings, onImported }: Props = $props();

	const uid = $props.id();
	let files = $state<FileList | null>(null);
	let busy = $state(false);
	let result = $state<{ level: 'info' | 'error'; text: string } | null>(null);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		const file = files?.[0];
		if (!file) {
			result = { level: 'error', text: 'Bitte eine YAML-Datei wählen.' };
			return;
		}
		busy = true;
		result = null;
		try {
			const imported = await importSettings(await file.text());
			result = { level: 'info', text: `Version ${imported.version} übernommen.` };
			onImported(imported);
		} catch (error) {
			result = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Datei nicht lesbar'
			};
		} finally {
			busy = false;
		}
	}
</script>

<p><a href={EXPORT_SETTINGS_URL} download>Exportieren (YAML)</a></p>
<form class="import" onsubmit={submit} novalidate>
	<label for="{uid}-file">YAML-Datei importieren</label>
	<input id="{uid}-file" type="file" accept=".yaml,.yml,text/yaml" bind:files />
	<p><button type="submit" disabled={busy}>Importieren</button></p>
	{#if result}
		<Notice level={result.level}>{result.text}</Notice>
	{/if}
</form>

<style>
	.import {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 0.5rem;
		max-width: 28rem;
	}

	.import input {
		width: 100%;
		min-width: 0;
	}

	p {
		margin: 0 0 0.5rem;
	}
</style>
