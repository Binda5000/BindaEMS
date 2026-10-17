<script lang="ts">
	import type { SupportIntent } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { formatNumberMax, formatSoc } from '$lib/format';
	import {
		parseSupportThreshold,
		supportHint,
		supportSourceLabel,
		type CardNotice,
		type SupportEffective
	} from './view';

	interface Props {
		/** Erlaubnis und eigene Schwelle der Absicht; `soc_pct` `null`: die der Konfiguration */
		support: SupportIntent;
		/** die wirksame Schwelle laut Health; `null` ohne Health */
		effective: SupportEffective | null;
		/** `control.support_soc_pct` der Konfiguration, in % */
		configured_pct: number;
		/** USV-Reserve in %: untere Grenze der Schwelle */
		reserve_pct: number;
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		onChange: (allowed: boolean, socPct: number | null) => Promise<void>;
	}

	let { support, effective, configured_pct, reserve_pct, operate, busy, notice, onChange }: Props =
		$props();

	const uid = $props.id();

	/** Eingabe im Zahlfeld, solange sie noch nicht übernommen ist; `null`: der Stand der Absicht */
	let draft = $state<string | null>(null);
	let badInput = $state(false);
	let problem = $state<string | null>(null);

	const text = $derived(draft ?? (support.soc_pct === null ? '' : String(support.soc_pct)));

	async function toggle(event: Event) {
		const box = event.currentTarget as HTMLInputElement;
		await onChange(box.checked, support.soc_pct);
		box.checked = support.allowed; // bei einer Ablehnung zurück auf den Stand des Servers
	}

	function type(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		draft = input.value;
		badInput = input.validity.badInput;
		problem = null;
	}

	async function apply() {
		const threshold = parseSupportThreshold(text, reserve_pct, badInput);
		if (!threshold.ok) {
			problem = threshold.text;
			return;
		}
		problem = null;
		await onChange(support.allowed, threshold.soc_pct);
		draft = null; // danach zeigt das Feld wieder den Stand der Absicht
		badInput = false;
	}
</script>

<Card title="Akku-Unterstützung">
	{#if operate}
		<div class="form">
			<label class="check">
				<input type="checkbox" checked={support.allowed} disabled={busy} onchange={toggle} />
				Akku-Unterstützung erlaubt
			</label>
			<div class="field">
				<label for="{uid}-soc">Unterstützungsschwelle</label>
				<div class="unit">
					<input
						id="{uid}-soc"
						type="number"
						min={reserve_pct}
						max="100"
						step="1"
						value={text}
						placeholder={formatNumberMax(configured_pct, 1)}
						disabled={busy}
						oninput={type}
					/>
					<span>%</span>
				</div>
			</div>
			<button type="button" disabled={busy} onclick={apply}>Übernehmen</button>
		</div>
		{#if problem}
			<div class="note"><Notice level="error">{problem}</Notice></div>
		{/if}
	{:else}
		<dl class="facts">
			<dt>Akku-Unterstützung erlaubt</dt>
			<dd>{support.allowed ? 'ja' : 'nein'}</dd>
			<dt>Unterstützungsschwelle</dt>
			<dd>
				{#if support.soc_pct === null}
					{formatSoc(configured_pct)} ({supportSourceLabel('config')})
				{:else}
					{formatSoc(support.soc_pct)}
				{/if}
			</dd>
		</dl>
	{/if}

	<p class="hint">{supportHint(effective)}</p>

	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}
</Card>

<style>
	.form {
		display: flex;
		flex-wrap: wrap;
		align-items: end;
		gap: 0.75rem;
	}

	.field {
		display: grid;
		gap: 0.25rem;
		min-width: 0;
	}

	label {
		font-weight: 600;
	}

	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex: 1 1 100%;
		min-height: 2.75rem;
	}

	.unit {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	input[type='number'] {
		width: 8rem;
		max-width: 100%;
	}

	.hint {
		margin: 0.75rem 0 0;
		color: var(--muted);
		font-size: 0.875rem;
	}

	.note {
		margin-top: 0.75rem;
	}
</style>
