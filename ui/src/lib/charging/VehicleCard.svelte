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
	import NowLimitSelect from './NowLimitSelect.svelte';
	import {
		assignmentLabel,
		CHARGE_MODES,
		modeLabel,
		nowLimitText,
		parseTarget,
		reasonLabel,
		SOC_ASSUMED_NOTE,
		SOC_ESTIMATED_NOTE,
		staleSocNote,
		vehicleStatus,
		type BoostInfo,
		type CardNotice,
		type NowLimitSetup,
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
		/** Höchststrom in „Sofort“ mit Bereich des Fahrzeugs; `null` ohne dessen Grenzen */
		nowLimit?: NowLimitSetup | null;
		/** schickt den Höchststrom in A; `null` ist volle Leistung */
		onNowLimit?: (maxA: number | null) => Promise<void>;
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
		onAutomation,
		nowLimit = null,
		onNowLimit
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

	// Ein Ladestand, der da, aber zu alt ist (der Plan rechnet ab 0 %)
	const staleSoc = $derived(!estimated && soc?.quality === 'stale' ? (soc.pct ?? null) : null);
	const socPct = $derived(estimated ? vehicle.soc_pct : (soc?.pct ?? null));
	const status = $derived(vehicleStatus(vehicle));
	const subtitle = $derived(
		[
			wallbox === null ? null : `${wallbox} (${assignmentLabel(vehicle.assignment)})`,
			modeLabel(vehicle.mode)
		]
			.filter((part) => part !== null)
			.join(' · ')
	);
	const barLabel = $derived(
		`Ladestand ${formatSoc(socPct)}` +
			(mode === 'planned' ? `, Ziel ${formatSoc(goal.target_soc_pct)}` : '')
	);

	// „geschätzt“ steht neben dem Ladestand: der Zusatz von „Geplant“ wiederholt es nicht. Bei einem
	// veralteten Ladestand erklärt `staleSocNote` den Satz „Ladestand unbekannt …“
	const notes = $derived(
		(planned?.notes ?? [])
			.filter((note) => !(estimated && note === SOC_ESTIMATED_NOTE))
			.map((note) =>
				note === SOC_ASSUMED_NOTE && staleSoc !== null ? staleSocNote(staleSoc) : note
			)
	);

	const costText = $derived(boostCostText(boostCost, vehicle.id));
	// Lesende sehen das Limit als Satz; Bedienende sehen es in der Auswahl. Solange Boost gilt,
	// ruht das Limit (Boost lädt mit voller Leistung): kein Satz
	const nowLimitLine = $derived(
		mode === 'now' && !operate && !boost.active && nowLimit !== null
			? nowLimitText(nowLimit.max_a, nowLimit.range, nowLimit.voltage)
			: null
	);

	const showPlan = $derived(
		planned !== null || nowLimitLine !== null || sessionLine !== null || boost.active
	);

	function clampPct(pct: number): number {
		return Math.min(100, Math.max(0, pct));
	}

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

<Card title={vehicle.name} {subtitle}>
	{#snippet aside()}
		<span class="status" class:ok={status.tone === 'ok'}>{status.text}</span>
	{/snippet}

	<div class="soc">
		<p class="soc-line">
			<span class="soc-value">
				{#if estimated}
					<strong>{formatSoc(vehicle.soc_pct)}</strong> <span class="tag">geschätzt</span>
				{:else}
					<strong>{formatSoc(soc?.pct)}</strong>
					{#if soc?.quality === 'stale'}<span class="tag warn">veraltet</span>{/if}
				{/if}
			</span>
			{#if mode === 'planned'}
				<span class="target-label">Ziel {formatSoc(goal.target_soc_pct)}</span>
			{/if}
		</p>
		{#if socPct !== null}
			<div class="bar" role="img" aria-label={barLabel}>
				<div class="fill" style:width="{clampPct(socPct)}%"></div>
				{#if mode === 'planned'}
					<div class="mark" style:left="{clampPct(goal.target_soc_pct)}%"></div>
				{/if}
			</div>
		{/if}
	</div>

	{#if showPlan}
		<div class="plan">
			{#if boost.active && boost.until !== null}
				<p>
					<strong>Boost</strong>
					<span>bis {formatTime(boost.until)}</span>
					{#if boost.pending}<span class="muted">wartet auf Anstecken</span>{/if}
				</p>
			{/if}
			{#if planned}
				{#if planned.text !== null}<p>{planned.text}</p>{/if}
				{#each notes as note (note)}<p class="muted">{note}</p>{/each}
			{/if}
			{#if nowLimitLine !== null}<p>{nowLimitLine}</p>{/if}
			{#if sessionLine !== null}<p>{sessionLine}</p>{/if}
		</div>
	{/if}

	{#if operate}
		<h3>Laden einstellen</h3>
		<div class="controls">
			<label>
				Lademodus
				<select value={mode} disabled={busy} onchange={chooseMode}>
					{#each CHARGE_MODES as option (option)}
						<option value={option}>{modeLabel(option)}</option>
					{/each}
				</select>
			</label>
			{#if mode === 'now' && nowLimit !== null && onNowLimit}
				<NowLimitSelect setup={nowLimit} {busy} onChange={onNowLimit} />
			{/if}
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
				<button type="button" class="primary save" disabled={busy} onclick={applyTarget}>
					Ziel speichern
				</button>
			</div>
			{#if problem}
				<div class="note"><Notice level="error">{problem}</Notice></div>
			{/if}
		{/if}
		{#if costText !== null}
			<p class="muted cost">{costText}</p>
		{/if}
	{/if}

	<!-- Antwort auf „Ziel speichern“ und „Vorschlag übernehmen“: gleich unter den Knöpfen -->
	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}

	{#if driving}
		<h3>Vorschlag aus dem Fahrverhalten</h3>
		<div class="driving" role="group" aria-label="Vorschlag">
			{#if driving.next_trip}
				<p>{suggestionText(driving.next_trip)}</p>
				{#if operate}
					<button type="button" disabled={busy} onclick={adopt}>Vorschlag übernehmen</button>
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

	<details>
		<summary>Details</summary>
		<dl class="facts">
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
	</details>
</Card>

<style>
	.status {
		flex: none;
		padding: 0.125rem 0.625rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		color: var(--muted);
		font-size: 0.8125rem;
		white-space: nowrap;
	}

	.status.ok {
		border-color: var(--ok);
		color: var(--ok);
	}

	.soc-line {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.25rem 0.75rem;
		margin: 0 0 0.375rem;
	}

	.soc-value strong {
		font-size: 1.75rem;
		font-weight: 700;
		line-height: 1.2;
	}

	.target-label {
		color: var(--muted);
	}

	.tag {
		display: inline-block;
		padding: 0 0.5rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		color: var(--muted);
		font-size: 0.8125rem;
		vertical-align: middle;
	}

	.tag.warn {
		border-color: var(--warn);
	}

	.bar {
		position: relative;
		height: 0.625rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		background: var(--surface-2);
		overflow: hidden;
	}

	.fill {
		height: 100%;
		background: var(--accent);
	}

	.mark {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 2px;
		margin-left: -1px;
		background: var(--text);
	}

	.plan {
		margin-top: 0.75rem;
		padding: 0.625rem 0.75rem;
		border: 1px solid var(--border);
		border-radius: 8px;
		background: var(--surface-2);
		overflow-wrap: anywhere;
	}

	.plan p {
		margin: 0;
	}

	.plan p + p {
		margin-top: 0.25rem;
	}

	h3 {
		margin: 1.25rem 0 0.5rem;
		font-size: 0.9375rem;
		font-weight: 600;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: end;
		gap: 0.75rem;
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
		display: grid;
		grid-template-columns: repeat(2, minmax(0, auto));
		justify-content: start;
		align-items: end;
		gap: 0.75rem 1rem;
		margin-top: 0.75rem;
	}

	.save {
		grid-column: 1 / -1;
		width: 100%;
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

	.cost {
		margin: 0.5rem 0 0;
	}

	.driving {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.75rem;
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

	details {
		margin-top: 1rem;
		border-top: 1px solid var(--border);
	}

	details .facts {
		padding-bottom: 0.5rem;
	}

	.note {
		margin-top: 0.75rem;
	}
</style>
