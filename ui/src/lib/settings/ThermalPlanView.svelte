<script lang="ts">
	// Vorheizen und Vorkühlen in der Leseansicht der Einstellungen (Plan 5c)
	import type { LearningSettings } from '$lib/api/schemas';
	import { NBSP, formatCt, formatNumber } from '$lib/format';
	import { DEFAULT_THERMAL_PLAN } from './model';

	interface Props {
		learning: LearningSettings;
	}

	let { learning }: Props = $props();

	const thermal = $derived(learning.thermal ?? DEFAULT_THERMAL_PLAN);
	const kelvin = (k: number) => `${formatNumber(k, 1)}${NBSP}K`;
</script>

<dl class="facts">
	<dt>Vorheizen und Vorkühlen</dt>
	<dd>{thermal.enabled ? 'ein' : 'aus'}</dd>
	<dt>Heizen: vorheizen bis (K)</dt>
	<dd>{kelvin(thermal.heat_up_k)}</dd>
	<dt>Heizen: absenken bis (K)</dt>
	<dd>{kelvin(thermal.heat_down_k)}</dd>
	<dt>Kühlen: vorkühlen bis (K)</dt>
	<dd>{kelvin(thermal.cool_up_k)}</dd>
	<dt>Kühlen: wärmer werden lassen bis (K)</dt>
	<dd>{kelvin(thermal.cool_down_k)}</dd>
	<dt>Mindestvorteil bei Netzbezug (ct/kWh)</dt>
	<dd>{formatCt(thermal.margin_ct)}</dd>
</dl>
