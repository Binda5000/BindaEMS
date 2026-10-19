<script lang="ts">
	import type { PlanResponse } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { DASH, formatEur, formatNumber, formatTime, NBSP } from '$lib/format';
	import { plannedLine } from '$lib/charging/view';
	import { SOURCE_LABELS, TRIGGER_LABELS } from './model';

	interface Props {
		data: PlanResponse;
		canOperate: boolean;
		/** „Neu planen“ wurde ausgelöst und der neue Plan ist noch nicht da */
		replanning?: boolean;
		/** der Schalter wird gerade gesetzt */
		switching?: boolean;
		/** setzt „Netzladen erlaubt“; der Schalter folgt danach dem Stand in `data` */
		onGridCharge?: (on: boolean) => Promise<void>;
		onReplan?: () => void;
	}

	let {
		data,
		canOperate,
		replanning = false,
		switching = false,
		onGridCharge,
		onReplan
	}: Props = $props();

	const plan = $derived(data.plan);
	// ob der Plan noch gilt, sagt allein der Server (`plan.valid`, alle 60 s neu); die Uhr des Browsers
	// würde bei einer Abweichung dauerhaft „abgelaufen“ zeigen
	const valid = $derived(plan?.valid ?? false);
	const running = $derived(replanning || data.status.running);
	// Budget der akzeptierten Monatsspitze: der erste Slot mit Wert (nur mit Leistungspreis)
	const budget = $derived(plan?.slots.find((slot) => slot.budget_kw !== null)?.budget_kw ?? null);
	// Ohne Plan steht der Status allein; mit Plan ergänzt er, was der Kopf nicht selbst sagt (z. B.
	// warum der letzte Lauf scheiterte). Ein gültiger Ersatzplan nennt seinen Grund schon im Kopf.
	const statusLine = $derived(
		plan === null || (!data.status.ok && !(plan.source === 'fallback' && valid))
			? data.status.message
			: null
	);

	// je geplantem Auto die Zeile wie in der Fahrzeugkarte; der Zeitplan kennt nur den Plan
	const cars = $derived(
		(plan?.vehicles ?? []).map((car) => ({
			key: car.key,
			name: car.name,
			line: plannedLine({ mode: null, vehicle: null, car, target: null })
		}))
	);

	async function toggle(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		await onGridCharge?.(input.checked);
		input.checked = data.grid_charge_allowed; // bei einer Ablehnung zurück auf den Stand des Servers
	}
</script>

<Card title={plan ? `Plan von ${formatTime(plan.created_at)} Uhr` : 'Plan'}>
	<!-- Stand der Ausführung im core; ohne gültigen Plan gibt es keinen (`execution` ist `null`) -->
	{#if data.execution}
		<p class="execution" class:live={data.execution.state === 'live'}>{data.execution.text}</p>
	{/if}
	{#if plan}
		<p class="line">
			<span class="tag" class:fallback={plan.source === 'fallback'}
				>{SOURCE_LABELS[plan.source]}</span
			>
			{#if valid}
				<span>gültig bis {formatTime(plan.valid_until)} Uhr</span>
			{:else}
				<span class="expired">Plan abgelaufen</span>
			{/if}
		</p>
		{#if plan.source === 'fallback' && plan.fallback_reason}
			<p class="muted">Grund: {plan.fallback_reason}</p>
		{/if}
		<p class="muted">Anlass: {TRIGGER_LABELS[plan.trigger]}</p>
		<dl class="facts costs">
			<dt>Erwartete Kosten</dt>
			<dd>{formatEur(plan.expected_cost_eur)}</dd>
			<dt>Victron allein</dt>
			<dd>{formatEur(plan.baseline_cost_eur)}</dd>
			{#if plan.power_price}
				<dt>Budget</dt>
				<dd>{budget === null ? DASH : `${formatNumber(budget, 1)}${NBSP}kW`}</dd>
			{/if}
		</dl>
		{#if cars.length > 0}
			<ul class="cars" aria-label="Geplante Autos">
				{#each cars as car (car.key)}
					<li>
						<strong>{car.name}</strong>
						{#if car.line?.text}<span>{car.line.text}</span>{/if}
						{#each car.line?.notes ?? [] as note (note)}<span class="muted">{note}</span>{/each}
					</li>
				{/each}
			</ul>
		{/if}
	{:else}
		<p>Noch kein Plan.</p>
	{/if}

	{#if statusLine}
		<p class="status" role="status">{statusLine}</p>
	{/if}

	{#if canOperate}
		<div class="controls">
			<label class="switch">
				<input
					type="checkbox"
					role="switch"
					checked={data.grid_charge_allowed}
					disabled={switching}
					onchange={toggle}
				/>
				Netzladen erlaubt
			</label>
			<button type="button" disabled={running} onclick={() => onReplan?.()}>
				{running ? 'Plant …' : 'Neu planen'}
			</button>
		</div>
	{:else}
		<dl class="facts">
			<dt>Netzladen erlaubt</dt>
			<dd>{data.grid_charge_allowed ? 'ja' : 'nein'}</dd>
		</dl>
		{#if running}
			<p class="muted" role="status">Plant …</p>
		{/if}
	{/if}

	{#if plan && plan.issues.length > 0}
		<h3>Auffälligkeiten</h3>
		<ul class="issues">
			{#each plan.issues as issue, index (index)}
				<li>{issue}</li>
			{/each}
		</ul>
	{/if}
</Card>

<style>
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 0.75rem;
		margin: 0 0 0.5rem;
	}

	/* nur `live` ist neutral: Jeder andere Stand heißt, dass der core den Plan nicht ausführt */
	.execution {
		color: var(--warn);
		font-weight: 600;
	}

	.execution.live {
		color: inherit;
		font-weight: normal;
	}

	.tag {
		padding: 0.125rem 0.5rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		background: var(--surface-2);
		font-size: 0.875rem;
		font-weight: 600;
	}

	.tag.fallback {
		border-color: var(--warn);
	}

	.expired {
		color: var(--warn);
		font-weight: 600;
	}

	p {
		margin: 0 0 0.5rem;
	}

	.costs {
		margin-bottom: 0.75rem;
	}

	.cars {
		margin: 0 0 0.75rem;
		padding: 0;
		list-style: none;
		overflow-wrap: anywhere;
	}

	.cars li {
		display: flex;
		flex-wrap: wrap;
		gap: 0 0.5rem;
	}

	.status {
		margin: 0.5rem 0;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1.5rem;
		margin-top: 0.5rem;
	}

	/* Das Feld ist 44 px hoch, auch wenn der Schalter selbst klein ist */
	.switch {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-height: 2.75rem;
		font-weight: 600;
	}

	.switch input {
		flex: none;
	}

	h3 {
		margin: 1rem 0 0.25rem;
		font-size: 0.9375rem;
		font-weight: 600;
	}

	.issues {
		margin: 0;
		padding-left: 1.25rem;
	}
</style>
