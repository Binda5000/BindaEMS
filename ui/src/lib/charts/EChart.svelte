<script lang="ts">
	import type { EChartsCoreOption, EChartsType } from 'echarts/core';
	import { onMount } from 'svelte';
	import { loadECharts } from './echarts';

	interface Props {
		option: EChartsCoreOption;
		label: string;
		height?: string;
	}

	let { option, label, height = '18rem' }: Props = $props();

	let element = $state<HTMLDivElement>();
	let chart = $state.raw<EChartsType | null>(null);

	onMount(() => {
		let disposed = false;
		let observer: ResizeObserver | undefined;
		void loadECharts().then((echarts) => {
			if (disposed || !element) return;
			const instance = echarts.init(element, null, { renderer: 'canvas' });
			if (typeof ResizeObserver !== 'undefined') {
				observer = new ResizeObserver(() => instance.resize());
				observer.observe(element);
			}
			chart = instance;
		});
		return () => {
			disposed = true;
			observer?.disconnect();
			chart?.dispose();
			chart = null;
		};
	});

	// erste und jede geänderte Option vollständig übernehmen
	$effect(() => {
		chart?.setOption(option, { notMerge: true });
	});
</script>

<div bind:this={element} class="chart" role="img" aria-label={label} style:height></div>

<style>
	.chart {
		width: 100%;
		min-width: 0;
	}
</style>
