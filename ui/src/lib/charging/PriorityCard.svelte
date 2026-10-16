<script lang="ts">
	import type { SurplusPriority } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import {
		moveDown,
		moveUp,
		SURPLUS_KINDS,
		surplusLabel,
		type CardNotice,
		type Names
	} from './view';

	interface Props {
		/** Schlüssel der Fahrzeuge, wichtigstes zuerst */
		order: string[];
		names: Names;
		surplus: SurplusPriority;
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		onChange: (order: string[], surplus: SurplusPriority) => Promise<void>;
	}

	let { order, names, surplus, operate, busy, notice, onChange }: Props = $props();

	async function chooseKind(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		await onChange(order, { ...surplus, kind: select.value as SurplusPriority['kind'] });
		select.value = surplus.kind; // bei einer Ablehnung zurück auf den Stand des Servers
	}

	async function chooseSoc(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const pct = input.valueAsNumber;
		if (Number.isFinite(pct) && pct >= 0 && pct <= 100) {
			await onChange(order, { ...surplus, soc_pct: pct });
		}
		input.value = String(surplus.soc_pct);
	}
</script>

<Card title="Vorrang">
	<h3>Reihenfolge der Fahrzeuge</h3>
	<ol class="order">
		{#each order as key, index (key)}
			<li>
				<span class="name">{names[key] ?? key}</span>
				{#if operate}
					<button
						type="button"
						aria-label="{names[key] ?? key} nach oben"
						disabled={busy || index === 0}
						onclick={() => onChange(moveUp(order, key), surplus)}
					>
						nach oben
					</button>
					<button
						type="button"
						aria-label="{names[key] ?? key} nach unten"
						disabled={busy || index === order.length - 1}
						onclick={() => onChange(moveDown(order, key), surplus)}
					>
						nach unten
					</button>
				{/if}
			</li>
		{/each}
	</ol>

	{#if operate}
		<div class="surplus">
			<label>
				Vorrang beim Überschuss
				<select value={surplus.kind} disabled={busy} onchange={chooseKind}>
					{#each SURPLUS_KINDS as kind (kind)}
						<option value={kind}>{surplusLabel({ kind, soc_pct: surplus.soc_pct })}</option>
					{/each}
				</select>
			</label>
			{#if surplus.kind === 'battery_until_soc'}
				<label>
					Akku-SOC in %
					<input
						type="number"
						min="0"
						max="100"
						step="1"
						value={surplus.soc_pct}
						disabled={busy}
						onchange={chooseSoc}
					/>
				</label>
			{/if}
		</div>
	{:else}
		<dl class="facts">
			<dt>Vorrang beim Überschuss</dt>
			<dd>{surplusLabel(surplus)}</dd>
		</dl>
	{/if}

	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}
</Card>

<style>
	h3 {
		margin: 0 0 0.5rem;
		font-size: 0.9375rem;
	}

	.order {
		list-style: decimal inside;
		margin: 0 0 1rem;
		padding: 0;
		display: grid;
		gap: 0.5rem;
	}

	.order li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}

	.name {
		flex: 1 1 8rem;
		font-weight: 600;
		min-width: 0;
	}

	.surplus {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	label {
		display: grid;
		gap: 0.25rem;
		font-weight: 600;
		min-width: 0;
	}

	select,
	input {
		max-width: 100%;
	}

	input {
		width: 8rem;
	}

	.note {
		margin-top: 0.75rem;
	}
</style>
