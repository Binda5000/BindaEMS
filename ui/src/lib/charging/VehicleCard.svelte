<script lang="ts">
	import type { BoostCost, ChargeMode, DrivingVehicle, VehicleHealth } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import {
		DASH,
		formatNumber,
		formatSoc,
		formatTime,
		NBSP,
		suggestionText,
		weekdayDate
	} from '$lib/format';
	import { todayVienna } from '$lib/time';
	import type { SocEntry } from '$lib/dashboard/socs';
	import { boostCostText } from './boostCost';
	import {
		assignmentLabel,
		CHARGE_MODES,
		modeLabel,
		parseTarget,
		reasonLabel,
		type BoostInfo,
		type CardNotice,
		type PlannedLine
	} from './view';

	interface Props {
		vehicle: VehicleHealth;
		/** Anzeigename der Wallbox, an der das Fahrzeug steht */
		wallbox: string | null;
		/** eingestellter Lademodus (Absicht), ohne Boost */
		mode: ChargeMode;
		boost: BoostInfo;
		soc?: SocEntry;
		/** Mehrkosten des Boosts; der Satz erscheint nur ab „Bedienen“ und mit `applies` */
		boostCost?: BoostCost | null;
		/** laufende oder letzte Ladesession in einer Zeile; ohne Session keine Zeile */
		sessionLine?: string | null;
		/** ab „Bedienen“ */
		operate: boolean;
		busy: boolean;
		notice: CardNotice | null;
		onMode: (mode: ChargeMode) => Promise<void>;
		onBoost: (on: boolean) => Promise<void>;
		/** Ziel-SOC, Abfahrt und Abfahrtstag (`YYYY-MM-DD`, `null` täglich) der Absicht (für „Geplant“) */
		goal: { target_soc_pct: number; departure: string; departure_on?: string | null };
		/** Zeile und Zusätze von „Geplant“ (`plannedLine`); `null` ohne */
		planned: PlannedLine | null;
		/** schickt Ziel-SOC und Abfahrt; `null` lässt das Feld unverändert, nie beide */
		onTarget: (targetSocPct: number | null, departure: string | null) => Promise<void>;
		/** Fahrverhalten des Autos (Vorschlag und Automatik); `null` ohne Antwort des Servers */
		driving?: DrivingVehicle | null;
		/** übernimmt den Vorschlag: Ziel (`null` ohne), Abfahrt `HH:MM` und Abfahrtstag `YYYY-MM-DD` */
		onAdopt?: (
			targetSocPct: number | null,
			departure: string,
			departureOn: string
		) => Promise<void>;
		/** schaltet die Automatik für Ziel und Abfahrt */
		onAutomation?: (on: boolean) => Promise<void>;
	}

	let {
		vehicle,
		wallbox,
		mode,
		boost,
		soc,
		boostCost = null,
		sessionLine = null,
		operate,
		busy,
		notice,
		onMode,
		onBoost,
		goal,
		planned,
		onTarget,
		driving = null,
		onAdopt,
		onAutomation
	}: Props = $props();

	const uid = $props.id();

	/** Eingaben, solange sie nicht übernommen sind; `null`: der Stand der Absicht */
	let socDraft = $state<string | null>(null);
	let departureDraft = $state<string | null>(null);
	let badInput = $state(false);
	let problem = $state<string | null>(null);

	const socText = $derived(socDraft ?? String(goal.target_soc_pct));
	const departureText = $derived(departureDraft ?? goal.departure);
	// Abfahrt an einem Tag nach heute (Ruling F-3): „am Mo 23.11.“ neben dem Feld. Eine
	// geänderte Uhrzeit gilt danach täglich (die app löscht den Tag), dann fehlt der Hinweis.
	const departureDay = $derived(
		goal.departure_on && goal.departure_on > todayVienna() && departureText === goal.departure
			? weekdayDate(goal.departure_on)
			: null
	);
	// Ein geschätzter Ladestand (Health) sagt mehr als der zuletzt gemessene Wert des Signals
	const estimated = $derived(vehicle.soc_source === 'estimated' && vehicle.soc_pct !== null);

	const costText = $derived(boostCostText(boostCost, vehicle.id));

	function yesNo(value: boolean | null): string {
		return value === null ? DASH : value ? 'ja' : 'nein';
	}

	function typeSoc(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		socDraft = input.value;
		badInput = input.validity.badInput;
		problem = null;
	}

	function typeDeparture(event: Event) {
		departureDraft = (event.currentTarget as HTMLInputElement).value;
		problem = null;
	}

	async function applyTarget() {
		const parsed = parseTarget(socText, departureText, goal, badInput);
		if (!parsed.ok) {
			problem = parsed.text;
			return;
		}
		problem = null;
		if (parsed.target_soc_pct !== null || parsed.departure !== null) {
			await onTarget(parsed.target_soc_pct, parsed.departure);
		}
		socDraft = null; // danach zeigen die Felder wieder den Stand der Absicht
		departureDraft = null;
		badInput = false;
	}

	async function adopt() {
		const trip = driving?.next_trip;
		if (!trip) return;
		await onAdopt?.(trip.target_soc_pct, trip.departure, trip.date);
	}

	async function toggleAutomation(event: Event) {
		const box = event.currentTarget as HTMLInputElement;
		await onAutomation?.(box.checked);
		box.checked = driving?.automation.enabled ?? false; // bei einer Ablehnung zurück auf den Stand des Servers
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
			{#if estimated}
				{formatSoc(vehicle.soc_pct)} <span class="muted">geschätzt</span>
			{:else}
				{formatSoc(soc?.pct)}
				{#if soc?.quality === 'stale'}<span class="muted">veraltet</span>{/if}
			{/if}
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
	{#if planned}
		<div class="planned">
			{#if planned.text !== null}<p>{planned.text}</p>{/if}
			{#each planned.notes as note (note)}<p class="muted">{note}</p>{/each}
		</div>
	{/if}
	{#if sessionLine !== null}
		<p class="session">{sessionLine}</p>
	{/if}

	{#if driving}
		<div class="driving" role="group" aria-label="Vorschlag">
			{#if driving.next_trip}
				<p>{suggestionText(driving.next_trip)}</p>
				{#if operate}
					<button type="button" disabled={busy} onclick={adopt}>Übernehmen</button>
				{/if}
			{:else}
				<p class="muted">Kein Vorschlag (zu wenig Daten).</p>
			{/if}
		</div>
		<div class="automation">
			<label class="switch">
				<input
					type="checkbox"
					role="switch"
					checked={driving.automation.enabled}
					disabled={!operate || busy}
					aria-describedby="{uid}-automation"
					onchange={toggleAutomation}
				/>
				Ziel und Abfahrt automatisch
			</label>
			<p id="{uid}-automation" class="muted">{driving.automation.text}</p>
		</div>
	{/if}

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
		{#if mode === 'planned'}
			<div class="target" role="group" aria-label="Von Hand">
				<div class="field">
					<label for="{uid}-soc">Ziel-SOC</label>
					<div class="unit">
						<input
							id="{uid}-soc"
							type="number"
							min="20"
							max="100"
							step="1"
							value={socText}
							disabled={busy}
							oninput={typeSoc}
						/>
						<span>%</span>
					</div>
				</div>
				<div class="field">
					<label for="{uid}-departure">Abfahrt</label>
					<div class="unit">
						<input
							id="{uid}-departure"
							type="time"
							value={departureText}
							disabled={busy}
							aria-describedby={departureDay === null ? undefined : `${uid}-day`}
							oninput={typeDeparture}
						/>
						{#if departureDay !== null}
							<span id="{uid}-day" class="muted">am {departureDay}</span>
						{/if}
					</div>
				</div>
				<button type="button" disabled={busy} onclick={applyTarget}>Übernehmen</button>
			</div>
			{#if problem}
				<div class="note"><Notice level="error">{problem}</Notice></div>
			{/if}
		{/if}
		{#if costText !== null}
			<p class="muted cost">{costText}</p>
		{/if}
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

	.target {
		display: flex;
		flex-wrap: wrap;
		align-items: end;
		gap: 0.75rem;
		margin-top: 0.75rem;
	}

	.field {
		display: grid;
		gap: 0.25rem;
		min-width: 0;
	}

	.field label {
		display: block;
	}

	.unit {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	input[type='number'] {
		width: 6rem;
		max-width: 100%;
	}

	input[type='time'] {
		max-width: 100%;
	}

	.planned {
		margin: 0.75rem 0 0;
		overflow-wrap: anywhere;
	}

	.planned p {
		margin: 0;
	}

	.session {
		margin: 0.75rem 0 0;
		overflow-wrap: anywhere;
	}

	.cost {
		margin: 0.5rem 0 0;
	}

	.driving {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.75rem;
		margin-top: 0.75rem;
	}

	.driving p,
	.automation p {
		margin: 0;
		overflow-wrap: anywhere;
	}

	.automation {
		margin-top: 0.5rem;
	}

	/* Das Feld ist 44 px hoch, auch wenn der Schalter selbst klein ist */
	.switch {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-height: 2.75rem;
	}

	.switch input {
		flex: none;
	}

	.note {
		margin-top: 0.75rem;
	}
</style>
