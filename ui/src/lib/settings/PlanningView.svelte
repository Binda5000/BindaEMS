<script lang="ts">
	// Planung in der Leseansicht der Einstellungen
	import type { PlanningSettings } from '$lib/api/schemas';
	import { formatNumber, NBSP } from '$lib/format';

	interface Props {
		planning: PlanningSettings;
	}

	let { planning }: Props = $props();
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
</dl>
