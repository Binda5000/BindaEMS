<script lang="ts">
	import type { ChargeMode } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import NowLimitSelect from './NowLimitSelect.svelte';
	import {
		GUEST_MODES,
		modeLabel,
		nowLimitText,
		type CardNotice,
		type NowLimitSetup
	} from './view';

	interface Props {
		mode: ChargeMode;
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		onMode: (mode: ChargeMode) => Promise<void>;
		/** Höchststrom in „Sofort“ mit Bereich und Phasen der EVCS; `null` ohne EVCS */
		nowLimit?: NowLimitSetup | null;
		/** schickt den Höchststrom in A; `null` ist volle Leistung */
		onNowLimit?: (maxA: number | null) => Promise<void>;
	}

	let { mode, operate, busy, notice, onMode, nowLimit = null, onNowLimit }: Props = $props();

	// Lesende sehen das Limit als Satz; Bedienende sehen es in der Auswahl
	const nowLimitLine = $derived(
		mode === 'now' && !operate && nowLimit !== null
			? nowLimitText(nowLimit.max_a, nowLimit.range, nowLimit.voltage)
			: null
	);

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
		{#if mode === 'now' && nowLimit !== null && onNowLimit}
			<div class="limit"><NowLimitSelect setup={nowLimit} {busy} onChange={onNowLimit} /></div>
		{/if}
	{:else}
		<dl class="facts">
			<dt>Lademodus</dt>
			<dd>{modeLabel(mode)}</dd>
		</dl>
		{#if nowLimitLine !== null}
			<p class="line">{nowLimitLine}</p>
		{/if}
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

	.limit {
		margin-top: 0.75rem;
	}

	.line {
		margin: 0.75rem 0 0;
		overflow-wrap: anywhere;
	}

	.note {
		margin-top: 0.75rem;
	}
</style>
