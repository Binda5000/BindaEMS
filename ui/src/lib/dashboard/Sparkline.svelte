<script lang="ts">
	import { sparkLine } from './tiles';

	interface Props {
		points: [number, number][];
		/** CSS-Farbe, etwa `var(--pv)` */
		color: string;
	}

	let { points, color }: Props = $props();

	const line = $derived(sparkLine(points));
</script>

{#if line}
	<svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" focusable="false">
		<polyline points={line} fill="none" stroke={color} stroke-width="2" />
	</svg>
{/if}

<style>
	svg {
		display: block;
		width: 100%;
		height: 1.5rem;
	}

	polyline {
		vector-effect: non-scaling-stroke;
		stroke-linejoin: round;
		stroke-linecap: round;
	}
</style>
