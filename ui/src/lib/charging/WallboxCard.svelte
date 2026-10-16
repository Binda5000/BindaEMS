<script lang="ts">
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import type { WallboxHealth } from '$lib/api/schemas';
	import {
		assignmentLabel,
		indicatorText,
		occupantText,
		type CardNotice,
		type Names
	} from './view';

	interface Props {
		wallbox: WallboxHealth;
		/** Anzeigename der Wallbox */
		title: string;
		names: Names;
		/** Schlüssel des Fahrzeugs, das auf diese Wallbox übersteuert ist */
		assigned: string | null;
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		/** `null`: „Automatisch“ */
		onAssign: (vehicle: string | null) => Promise<void>;
	}

	let { wallbox, title, names, assigned, operate, busy, notice, onAssign }: Props = $props();

	async function choose(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		await onAssign(select.value === '' ? null : select.value);
		select.value = assigned ?? ''; // bei einer Ablehnung zurück auf den Stand des Servers
	}
</script>

<Card {title}>
	<dl class="facts">
		<dt>Fahrzeug</dt>
		<dd>{occupantText(wallbox, names)}</dd>
		{#if wallbox.assignment !== null}
			<dt>Art</dt>
			<dd>{assignmentLabel(wallbox.assignment)}</dd>
		{/if}
	</dl>

	{#if wallbox.indicators.length > 0}
		<h3>Indizien</h3>
		<ul class="clues">
			{#each wallbox.indicators as indicator, index (index)}
				<li class:excludes={!indicator.supports}>{indicatorText(indicator, names)}</li>
			{/each}
		</ul>
	{/if}

	{#if operate}
		<label>
			Zuordnung
			<select value={assigned ?? ''} disabled={busy} onchange={choose}>
				<option value="">Automatisch</option>
				{#each Object.entries(names) as [key, name] (key)}
					<option value={key}>{name}</option>
				{/each}
			</select>
		</label>
	{/if}

	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}
</Card>

<style>
	h3 {
		margin: 1rem 0 0.5rem;
		font-size: 0.9375rem;
	}

	.clues {
		margin: 0;
		padding-left: 1.25rem;
		display: grid;
		gap: 0.25rem;
	}

	.excludes {
		color: var(--muted);
	}

	label {
		display: grid;
		gap: 0.25rem;
		margin-top: 1rem;
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
