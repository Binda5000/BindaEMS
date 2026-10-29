<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { DrivingStatus, DrivingVehicle, DrivingWeekday } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import {
		CONFIDENCE_LABELS,
		DASH,
		NBSP,
		WEEKDAY_LABELS,
		drivingSourceText,
		formatNumber,
		formatSoc,
		weakestConfidence,
		weekdayGroup
	} from '$lib/format';
	import { groupDays } from './view';

	interface Props {
		data: DrivingStatus | undefined;
		error: ApiError | null;
		/** Taste „Jetzt neu berechnen“ nur für Admins */
		admin: boolean;
		/** Der Start läuft gerade (zwischen Klick und Antwort) */
		starting?: boolean;
		/** Meldung zur Taste (Fehler beim Start) */
		message?: string | null;
		/** HA-Entität der Anwesenheit je Auto aus den Einstellungen; fehlt sie, steht nur der Name */
		presenceEntities?: Record<string, string>;
		onRecompute: () => void;
	}

	let {
		data,
		error,
		admin,
		starting = false,
		message = null,
		presenceEntities = {},
		onRecompute
	}: Props = $props();

	const uid = $props.id();
	const running = $derived(starting || (data?.running ?? false));

	function head(car: DrivingVehicle): string {
		const source =
			car.source === null
				? ''
				: ` · Quelle: ${drivingSourceText(car.source, presenceEntities[car.key])}`;
		return `${car.name}${source} · ${car.days} Tage`;
	}

	/** Autos, deren Tabelle jeden Tag einzeln zeigt (Standard: gleiche Tage in einer Zeile) */
	let single = $state<Record<string, boolean>>({});

	/** Die Zellen eines Tages ohne die Tagesspalte; Abfahrt und „meist“ stehen getrennt, der Rest ist
	 *  der Hinweis, aus welcher Gruppe das Profil rechnet (leer bei eigenen Tagen) */
	function dayCells(day: DrivingWeekday): string[] {
		return [
			`${day.trips}${NBSP}(${day.trip_pct}${NBSP}%)`,
			day.departure ?? 'keine Fahrt erwartet',
			day.departure !== null && day.departure_p50 !== null ? `meist ${day.departure_p50}` : '',
			day.arrival_p50 ?? DASH,
			formatSoc(day.arrival_soc_p50),
			percent(day.consumption_p80_pp),
			target(day),
			confidence(day),
			day.basis === 'group' ? weekdayGroup(day.weekday) : ''
		];
	}

	interface DayRow {
		label: string;
		cells: string[];
	}

	/** Gleiche aufeinanderfolgende Tage zusammen („Mo bis Fr“) */
	function groupedRows(car: DrivingVehicle): DayRow[] {
		// die Gruppe steht als letzte Zelle, damit nur Tage mit gleicher Grundlage zusammenfallen
		return groupDays(car.weekdays, (day) => WEEKDAY_LABELS[day.weekday] ?? DASH, dayCells).map(
			(row) => {
				const group = row.cells[row.cells.length - 1];
				return { label: group ? `${row.label} (aus ${group})` : row.label, cells: row.cells };
			}
		);
	}

	/** Zeilen der Tabelle: je Tag oder gleiche aufeinanderfolgende Tage zusammen */
	function dayRows(car: DrivingVehicle): DayRow[] {
		if (single[car.key]) {
			return car.weekdays.map((day) => ({ label: dayLabel(day), cells: dayCells(day) }));
		}
		return groupedRows(car);
	}

	/** „Tage einzeln“ lohnt nur, wenn das Zusammenfassen etwas zusammengefasst hat */
	function canSplit(car: DrivingVehicle): boolean {
		return groupedRows(car).length < car.weekdays.length;
	}

	/** „Mo“, bei zu wenigen Tagen des Wochentags „Mo (aus Mo–Fr)“ */
	function dayLabel(day: DrivingWeekday): string {
		const name = WEEKDAY_LABELS[day.weekday] ?? DASH;
		return day.basis === 'group' ? `${name} (aus ${weekdayGroup(day.weekday)})` : name;
	}

	function percent(value: number | null): string {
		return value === null ? DASH : `${formatNumber(value, 0)}${NBSP}%`;
	}

	function target(day: DrivingWeekday): string {
		if (day.target_soc_pct === null) return DASH;
		return day.target_clamped
			? `${formatSoc(day.target_soc_pct)} (begrenzt)`
			: formatSoc(day.target_soc_pct);
	}

	function confidence(day: DrivingWeekday): string {
		const weakest = weakestConfidence(day.departure_confidence, day.target_confidence);
		return weakest === null ? DASH : CONFIDENCE_LABELS[weakest];
	}

	/** „89 % (19 Tage)“; ohne bewertete Tage „– (noch zu wenig Tage)“ */
	function rate(pct: number | null, n: number): string {
		return n === 0 ? `${DASH} (noch zu wenig Tage)` : `${percent(pct)} (${n} Tage)`;
	}

	/** „Trefferquote 28 Tage: Abfahrt 89 % (19 Tage), Ziel 94 % (17 Tage)“ oder „noch zu wenig Tage“ */
	function quality(car: DrivingVehicle): string {
		const q = car.quality;
		if (q.departure_n === 0 && q.target_n === 0) return 'Trefferquote: noch zu wenig Tage';
		return (
			`Trefferquote ${q.days} Tage: Abfahrt ${rate(q.departure_hit_pct, q.departure_n)}, ` +
			`Ziel ${rate(q.target_hit_pct, q.target_n)}`
		);
	}
</script>

<Card title="Fahrverhalten">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		<p class="status">
			<StatusDot
				state={data.status.ok ? 'ok' : 'error'}
				label={data.status.ok ? 'in Ordnung' : 'gestört'}
			/>
			{data.status.text}
		</p>
		{#if admin}
			<button type="button" disabled={running} onclick={onRecompute}>
				{running ? 'Berechnung läuft …' : 'Jetzt neu berechnen'}
			</button>
		{:else if running}
			<p class="muted">Berechnung läuft …</p>
		{/if}
		{#if message}
			<Notice level="error">{message}</Notice>
		{/if}

		{#each data.vehicles as car (car.key)}
			<div class="car">
				<h3 id="{uid}-{car.key}">{head(car)}</h3>
				{#if car.days === 0}
					<p class="muted">Noch keine Daten für dieses Auto.</p>
				{:else}
					<div class="scroll">
						<table class="stack" aria-labelledby="{uid}-{car.key}">
							<thead>
								<tr>
									<th scope="col">Tag</th>
									<th scope="col">Fahrten</th>
									<th scope="col">Abfahrt</th>
									<th scope="col">Ankunft</th>
									<th scope="col">Ladestand bei Ankunft</th>
									<th scope="col">Verbrauch</th>
									<th scope="col">Vorschlag Ziel</th>
									<th scope="col">Konfidenz</th>
								</tr>
							</thead>
							<tbody>
								{#each dayRows(car) as row (row.label)}
									<tr>
										<th scope="row">{row.label}</th>
										<td data-label="Fahrten">{row.cells[0]}</td>
										<td data-label="Abfahrt">
											<span class="times">
												<span>{row.cells[1]}</span>
												{#if row.cells[2]}
													<span class="muted usual">{row.cells[2]}</span>
												{/if}
											</span>
										</td>
										<td data-label="Ankunft">{row.cells[3]}</td>
										<td data-label="Ladestand bei Ankunft">{row.cells[4]}</td>
										<td data-label="Verbrauch">{row.cells[5]}</td>
										<td data-label="Vorschlag Ziel">{row.cells[6]}</td>
										<td data-label="Konfidenz">{row.cells[7]}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#if canSplit(car)}
						<button
							type="button"
							class="days"
							aria-pressed={single[car.key] ?? false}
							onclick={() => (single[car.key] = !single[car.key])}
						>
							Tage einzeln
						</button>
					{/if}
					<p class="muted quality">{quality(car)}</p>
				{/if}
			</div>
		{/each}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0 0 0.5rem;
	}

	.status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.car {
		margin-top: 1rem;
	}

	h3 {
		margin: 0 0 0.5rem;
		font-size: 0.9375rem;
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	th[scope='row'] {
		font-weight: 600;
		white-space: nowrap;
	}

	.usual {
		display: block;
		font-size: 0.8125rem;
	}

	/* am Handy rechtsbündig unter dem Wert */
	.times {
		display: block;
	}

	.days {
		margin-top: 0.5rem;
	}

	.days[aria-pressed='true'] {
		background: var(--accent);
		color: var(--accent-text);
		border-color: var(--accent);
	}

	.quality {
		margin: 0.5rem 0 0;
	}
</style>
