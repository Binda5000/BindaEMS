<script lang="ts">
	// Planung in der Leseansicht der Einstellungen
	import type { PlanningSettings } from '$lib/api/schemas';
	import { formatNumber, formatNumberMax, NBSP } from '$lib/format';
	import { STORM_KIND_LABELS } from '$lib/plan/model';
	import { STORM_KINDS, STORM_MIN_LEVEL_LABELS } from './model';

	interface Props {
		planning: PlanningSettings;
	}

	let { planning }: Props = $props();

	const storm = $derived(planning.storm);
	const kinds = $derived(
		STORM_KINDS.filter((kind) => storm.kinds.includes(kind))
			.map((kind) => STORM_KIND_LABELS[kind])
			.join(', ')
	);
</script>

<dl class="facts">
	<dt>Netzladen erlaubt</dt>
	<dd>{planning.grid_charge_allowed ? 'ja' : 'nein'}</dd>
	<dt>Akku ins Auto, wenn übrig</dt>
	<dd>{planning.car_battery_allowed ? 'ja' : 'nein'}</dd>
	<dt>Marge</dt>
	<dd>{formatNumber(planning.margin_ct, 1)}{NBSP}ct/kWh</dd>
	<dt>Roundtrip-Wirkungsgrad</dt>
	<dd>{formatNumber(planning.roundtrip_pct, 0)}{NBSP}%</dd>
	<dt>Verschleiß</dt>
	<dd>{formatNumber(planning.wear_ct, 1)}{NBSP}ct/kWh</dd>
	<dt>Vorsichtsfaktor Restwert</dt>
	<dd>{formatNumber(planning.end_value_factor, 2)}</dd>
	<dt>Planträgheit</dt>
	<dd>{formatNumber(planning.inertia_ct, 1)}{NBSP}ct/kWh</dd>
	<dt>Spitze vermeiden ohne Leistungspreis</dt>
	<dd>
		{planning.peak_soft_ct_per_kw > 0
			? `${formatNumberMax(planning.peak_soft_ct_per_kw, 1)}${NBSP}ct je kW und Monat`
			: 'aus'}
	</dd>
</dl>

<h3>Unwetter-Reserve</h3>
<dl class="facts">
	<dt>Eingeschaltet</dt>
	<dd>{storm.enabled ? 'ja' : 'nein'}</dd>
	<dt>Ziel-SOC</dt>
	<dd>{formatNumber(storm.target_soc_pct, 0)}{NBSP}%</dd>
	<dt>Warnstufe</dt>
	<dd>{STORM_MIN_LEVEL_LABELS[storm.min_level]}</dd>
	<dt>Warnarten</dt>
	<dd>{kinds}</dd>
</dl>

<style>
	h3 {
		margin: 1rem 0 0.25rem;
		font-size: 0.9375rem;
		font-weight: 600;
	}
</style>
