<script lang="ts">
	import type { ChargeMode, VehicleHealth } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { DASH, formatNumber, formatSoc, formatTime, NBSP } from '$lib/format';
	import type { SocEntry } from '$lib/dashboard/socs';
	import {
		assignmentLabel,
		CHARGE_MODES,
		modeLabel,
		reasonLabel,
		type BoostInfo,
		type CardNotice
	} from './view';

	interface Props {
		vehicle: VehicleHealth;
		/** Anzeigename der Wallbox, an der das Fahrzeug steht */
		wallbox: string | null;
		/** eingestellter Lademodus (Absicht), ohne Boost */
		mode: ChargeMode;
		boost: BoostInfo;
		soc?: SocEntry;
		/** ab „Bedienen“ */
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		onMode: (mode: ChargeMode) => Promise<void>;
		onBoost: (on: boolean) => Promise<void>;
	}

	let { vehicle, wallbox, mode, boost, soc, operate, busy, notice, onMode, onBoost }: Props =
		$props();

	function yesNo(value: boolean | null): string {
		return value === null ? DASH : value ? 'ja' : 'nein';
	}

	async function chooseMode(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		await onMode(select.value as ChargeMode);
		select.value = mode; // bei einer Ablehnung zurück auf den Stand des Servers
	}
</script>

<Card title={vehicle.name}>
	<dl class="facts">
		<dt>Ladestand</dt>
		<dd>
			{formatSoc(soc?.pct)}
			{#if soc?.quality === 'stale'}<span class="muted">veraltet</span>{/if}
		</dd>
		<dt>Modus</dt>
		<dd>{modeLabel(vehicle.mode)}</dd>
		{#if boost.active && boost.until !== null}
			<dt>Boost</dt>
			<dd>
				<span>bis {formatTime(boost.until)}</span>
				{#if boost.pending}<span class="muted">wartet auf Anstecken</span>{/if}
			</dd>
		{/if}
		<dt>Wallbox</dt>
		<dd>
			{#if wallbox === null}
				{DASH}
			{:else}
				{wallbox} ({assignmentLabel(vehicle.assignment)})
			{/if}
		</dd>
		<dt>Angesteckt</dt>
		<dd>{yesNo(vehicle.plugged)}</dd>
		<dt>Lädt</dt>
		<dd>{yesNo(vehicle.charging)}</dd>
		<dt>Grenze</dt>
		<dd>{vehicle.limit_a === null ? DASH : `${formatNumber(vehicle.limit_a, 0)}${NBSP}A`}</dd>
		<dt>Letzter Grund</dt>
		<dd>{reasonLabel(vehicle.reason)}</dd>
		{#if vehicle.commands_hour !== null}
			<dt>Befehle letzte Stunde</dt>
			<dd>{vehicle.commands_hour}</dd>
		{/if}
	</dl>

	{#if operate}
		<div class="controls">
			<label>
				Lademodus
				<select value={mode} disabled={busy} onchange={chooseMode}>
					{#each CHARGE_MODES as option (option)}
						<option value={option}>{modeLabel(option)}</option>
					{/each}
				</select>
			</label>
			<button type="button" disabled={busy} onclick={() => onBoost(!boost.active)}>
				{boost.active ? 'Boost beenden' : 'Boost'}
			</button>
		</div>
	{/if}

	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}
</Card>

<style>
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: end;
		gap: 0.75rem;
		margin-top: 1rem;
	}

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
