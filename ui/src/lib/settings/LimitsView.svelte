<script lang="ts">
	import type { Limits } from '$lib/api/schemas';
	import { formatNumber, NBSP } from '$lib/format';

	interface Props {
		limits: Limits;
	}

	let { limits }: Props = $props();

	const n = (value: number, unit: string, digits = 0) =>
		`${formatNumber(value, digits)}${NBSP}${unit}`;
	const yesNo = (value: boolean) => (value ? 'erlaubt' : 'gesperrt');
</script>

<p class="hint">
	Nur in config.yaml änderbar; UI, Home Assistant und Lernmodelle können sie nicht ändern.
</p>

<h3>Netz</h3>
<dl class="facts">
	<dt>Phasen</dt>
	<dd>{limits.grid.phases}</dd>
	<dt>Nennspannung</dt>
	<dd>{n(limits.grid.voltage_nominal_v, 'V')}</dd>
	<dt>Hausanschlusssicherung</dt>
	<dd>{n(limits.grid.fuse_a, 'A')} je Phase (Abstand {n(limits.grid.fuse_margin_a, 'A')})</dd>
</dl>

<h3>Akku</h3>
<dl class="facts">
	<dt>Nutzbar</dt>
	<dd>{n(limits.battery.usable_kwh, 'kWh', 1)}</dd>
	<dt>USV-Reserve</dt>
	<dd>{n(limits.battery.reserve_soc_pct, '%')}</dd>
	<dt>Höchster SOC</dt>
	<dd>{n(limits.battery.soc_max_pct, '%')}</dd>
	<dt>Laden höchstens</dt>
	<dd>{n(limits.battery.max_charge_w, 'W')}</dd>
	<dt>Entladen höchstens</dt>
	<dd>{n(limits.battery.max_discharge_w, 'W')}</dd>
</dl>

<h3>Victron</h3>
<dl class="facts">
	<dt>Netzladen höchstens</dt>
	<dd>{n(limits.victron.max_grid_charge_setpoint_w, 'W')}</dd>
	<dt>Dauerhafte Schreibvorgänge</dt>
	<dd>
		{limits.victron.persistent_writes.per_hour} je Stunde, {limits.victron.persistent_writes
			.per_day}
		je Tag
	</dd>
	<dt>Watchdog</dt>
	<dd>
		Lebenszeichen alle {n(limits.victron.watchdog.heartbeat_s, 's')}, Rückfall nach {n(
			limits.victron.watchdog.timeout_s,
			's'
		)}
	</dd>
</dl>

<h3>Wallboxen</h3>
<div class="scroll">
	<table>
		<thead>
			<tr>
				<th scope="col">Name</th>
				<th scope="col">Typ</th>
				<th scope="col">Strom</th>
				<th scope="col">Phasen</th>
				<th scope="col">Live-Steuerung</th>
			</tr>
		</thead>
		<tbody>
			{#each Object.entries(limits.wallboxes) as [name, wallbox] (name)}
				<tr>
					<td>{name}</td>
					<td>{wallbox.type === 'victron_evcs_ns' ? 'Victron EVCS NS' : 'Tesla Wall Connector'}</td>
					<td>
						{#if wallbox.type === 'victron_evcs_ns'}
							{wallbox.min_a}–{wallbox.max_a}{NBSP}A (sicher {wallbox.safe_a}{NBSP}A)
						{:else}
							bis {wallbox.max_a}{NBSP}A
						{/if}
					</td>
					<td>{wallbox.phase_map.join(', ')}</td>
					<td>{wallbox.type === 'victron_evcs_ns' ? yesNo(wallbox.live_allowed) : '–'}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<h3>Fahrzeuge</h3>
<div class="scroll">
	<table>
		<thead>
			<tr>
				<th scope="col">Fahrzeug</th>
				<th scope="col">Akku</th>
				<th scope="col">Phasen</th>
				<th scope="col">Strom</th>
				<th scope="col">Wallbox</th>
				<th scope="col">Live-Steuerung</th>
			</tr>
		</thead>
		<tbody>
			{#each Object.entries(limits.vehicles) as [key, vehicle] (key)}
				<tr>
					<td>{vehicle.name}</td>
					<td>{n(vehicle.usable_kwh, 'kWh', 1)}</td>
					<td>{vehicle.phases}</td>
					<td>{vehicle.min_a}–{vehicle.max_a}{NBSP}A</td>
					<td>{vehicle.default_wallbox}</td>
					<td>{yesNo(vehicle.live_allowed)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.hint {
		margin: 0 0 0.5rem;
		color: var(--muted);
		font-size: 0.9375rem;
	}

	h3 {
		margin: 1rem 0 0.5rem;
		font-size: 0.9375rem;
	}

	.scroll {
		overflow-x: auto;
	}

	table {
		min-width: max-content;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
	}
</style>
