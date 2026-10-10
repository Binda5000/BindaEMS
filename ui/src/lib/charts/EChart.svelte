<script lang="ts">
	import type { EChartsCoreOption, EChartsType } from 'echarts/core';
	import { onMount } from 'svelte';
	import { viennaTicks } from '$lib/time';
	import { loadECharts } from './echarts';

	interface Props {
		option: EChartsCoreOption;
		label: string;
		height?: string;
	}

	let { option, label, height = '18rem' }: Props = $props();

	let element = $state<HTMLDivElement>();
	let chart = $state.raw<EChartsType | null>(null);
	let zoom: [number, number] = [0, 100]; // sichtbarer Teil der Zeitachse in Prozent

	interface TimeAxis {
		type?: string;
		axisLabel?: object;
		axisTick?: object;
	}
	interface WithTimeAxis {
		xAxis?: TimeAxis | TimeAxis[];
		series?: { data?: unknown[] }[];
	}

	/** Zeitbereich aller Reihen, wenn die x-Achse eine Zeitachse ist */
	function timeExtent(value: WithTimeAxis): [number, number] | null {
		if (Array.isArray(value.xAxis) || value.xAxis?.type !== 'time') return null;
		let low = Infinity;
		let high = -Infinity;
		for (const series of value.series ?? []) {
			for (const point of series.data ?? []) {
				const time = Array.isArray(point) ? point[0] : null;
				if (typeof time !== 'number' || !Number.isFinite(time)) continue;
				low = Math.min(low, time);
				high = Math.max(high, time);
			}
		}
		return low < high ? [low, high] : null;
	}

	/** Marken der Zeitachse in Wiener Zeit für den sichtbaren Teil (ECharts nähme die des Browsers) */
	function ticksFor(instance: EChartsType): number[] | null {
		const extent = timeExtent(option as WithTimeAxis);
		if (extent === null) return null;
		const [low, high] = extent;
		const from = low + ((high - low) * zoom[0]) / 100;
		const to = low + ((high - low) * zoom[1]) / 100;
		return viennaTicks(from, to, Math.max(4, Math.floor(instance.getWidth() / 90)));
	}

	function withTicks(value: EChartsCoreOption, ticks: number[] | null): EChartsCoreOption {
		const axis = (value as WithTimeAxis).xAxis;
		if (ticks === null || axis === undefined || Array.isArray(axis)) return value;
		return {
			...value,
			xAxis: {
				...axis,
				axisLabel: { ...axis.axisLabel, customValues: ticks },
				axisTick: { ...axis.axisTick, customValues: ticks }
			}
		};
	}

	function updateTicks(instance: EChartsType): void {
		const ticks = ticksFor(instance);
		if (ticks === null) return;
		instance.setOption({
			xAxis: { axisLabel: { customValues: ticks }, axisTick: { customValues: ticks } }
		});
	}

	onMount(() => {
		let disposed = false;
		let observer: ResizeObserver | undefined;
		void loadECharts().then((echarts) => {
			if (disposed || !element) return;
			const instance = echarts.init(element, null, { renderer: 'canvas' });
			if (typeof ResizeObserver !== 'undefined') {
				observer = new ResizeObserver(() => {
					instance.resize();
					updateTicks(instance); // die Zahl der Marken hängt von der Breite ab
				});
				observer.observe(element);
			}
			instance.on('datazoom', (event) => {
				const params = event as {
					start?: number;
					end?: number;
					batch?: { start?: number; end?: number }[];
				};
				const range = params.batch?.[0] ?? params;
				if (typeof range.start === 'number' && typeof range.end === 'number') {
					zoom = [range.start, range.end];
					updateTicks(instance);
				}
			});
			chart = instance;
		});
		return () => {
			disposed = true;
			observer?.disconnect();
			chart?.dispose();
			chart = null;
		};
	});

	function reducedMotion(): boolean {
		return (
			typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
		);
	}

	// neue Werte einarbeiten: Zoom und ausgeblendete Reihen bleiben, Reihen und y-Achsen werden
	// ersetzt (abgewählte verschwinden); bei prefers-reduced-motion ohne Animation
	$effect(() => {
		if (!chart) return;
		const prepared = withTicks(option, ticksFor(chart));
		chart.setOption(reducedMotion() ? { ...prepared, animation: false } : prepared, {
			replaceMerge: ['series', 'yAxis']
		});
	});
</script>

<div bind:this={element} class="chart" role="img" aria-label={label} style:height></div>

<style>
	.chart {
		width: 100%;
		min-width: 0;
	}
</style>
