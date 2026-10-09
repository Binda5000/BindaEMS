// ECharts erst bei Bedarf laden (eigener Chunk) und nur die benötigten Teile registrieren
type ECharts = typeof import('echarts/core');

let loading: Promise<ECharts> | null = null;

export function loadECharts(): Promise<ECharts> {
	loading ??= Promise.all([
		import('echarts/core'),
		import('echarts/charts'),
		import('echarts/components'),
		import('echarts/renderers')
	]).then(([core, charts, components, renderers]) => {
		core.use([
			charts.LineChart,
			charts.BarChart,
			components.GridComponent,
			components.TooltipComponent,
			components.LegendComponent,
			components.DataZoomComponent,
			components.MarkLineComponent,
			renderers.CanvasRenderer
		]);
		return core;
	});
	return loading;
}
