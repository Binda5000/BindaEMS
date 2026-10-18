<script lang="ts">
	import type { ChargeMode } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { GUEST_MODES, modeLabel, type CardNotice } from './view';

	interface Props {
		mode: ChargeMode;
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		onMode: (mode: ChargeMode) => Promise<void>;
	}

	let { mode, operate, busy, notice, onMode }: Props = $props();

	async function choose(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		await onMode(select.value as ChargeMode);
		select.value = mode; // bei einer Ablehnung zurück auf den Stand des Servers
	}
</script>

<Card title="Gast">
	{#if operate}
		<label>
			Lademodus
			<select value={mode} disabled={busy} onchange={choose}>
				{#each GUEST_MODES as option (option)}
					<option value={option}>{modeLabel(option)}</option>
				{/each}
			</select>
		</label>
	{:else}
		<dl class="facts">
			<dt>Lademodus</dt>
			<dd>{modeLabel(mode)}</dd>
		</dl>
	{/if}

	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}
</Card>

<style>
	label {
		display: grid;
		gap: 0.25rem;
		font-weight: 600;
		min-width: 0;
	}

	select {
		max-width: 100%;
	}

	.note {
		margin-top: 0.75rem;
	}
</style>
