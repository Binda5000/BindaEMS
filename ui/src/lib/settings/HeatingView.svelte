<script lang="ts">
	// Ölheizung in der Leseansicht der Einstellungen; Fehlendes steht als „nicht eingetragen“
	import type { HeatingSettings } from '$lib/api/schemas';
	import { NBSP, formatNumber, formatNumberMax } from '$lib/format';

	interface Props {
		heating: HeatingSettings;
	}

	let { heating }: Props = $props();

	const NOT_SET = 'nicht eingetragen';
	const text = (value: number | null, digits: number, unit: string) =>
		value === null ? NOT_SET : `${formatNumber(value, digits)}${NBSP}${unit}`;
</script>

<dl class="facts">
	<dt>Nennleistung</dt>
	<dd>{text(heating.burner_kw, 1, 'kW')}</dd>
	<dt>Öl je Brennerstunde</dt>
	<dd>{text(heating.oil_l_per_h, 1, 'l/h')}</dd>
	<dt>Ölpreis</dt>
	<dd>
		{heating.oil_price_eur_l === null
			? NOT_SET
			: `${formatNumberMax(heating.oil_price_eur_l, 3)}${NBSP}€/l`}
	</dd>
	<dt>Leistungszahl der Klimaanlage beim Heizen</dt>
	<dd>{formatNumber(heating.ac_cop, 1)}</dd>
</dl>
