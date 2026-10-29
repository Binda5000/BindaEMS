// Legende als HTML-Chips unter dem Diagramm: Einträge aus der ECharts-Option lesen, Auswahl zurückschreiben
import type { EChartsCoreOption } from 'echarts/core';

export interface LegendItem {
	name: string;
	color: string;
	mark: 'line' | 'dashed' | 'area' | 'rect';
}

interface StyledSeries {
	name?: string;
	type?: string;
	lineStyle?: { color?: string; width?: number; type?: string };
	itemStyle?: { color?: string };
	areaStyle?: { color?: string };
}
type LegendEntry = string | { name?: string; icon?: string };
interface WithLegend {
	legend?: { data?: LegendEntry[] } | { data?: LegendEntry[] }[];
	series?: StyledSeries | StyledSeries[];
}

function legendOf(option: WithLegend): { data?: LegendEntry[] } | undefined {
	return Array.isArray(option.legend) ? option.legend[0] : option.legend;
}

function seriesOf(option: WithLegend): StyledSeries[] {
	return Array.isArray(option.series) ? option.series : option.series ? [option.series] : [];
}

function markOf(series: StyledSeries | undefined, icon: string | undefined): LegendItem['mark'] {
	if (icon === 'rect' || series?.type === 'bar') return 'rect';
	if (series?.lineStyle?.width === 0) return 'area';
	return series?.lineStyle?.type === 'dashed' ? 'dashed' : 'line';
}

/**
 * Einträge der Legende: Reihenfolge und Auswahl aus `legend.data`, sonst alle Reihen mit Namen.
 * Ohne `legend` in der Option gibt es keine.
 */
export function legendItems(option: EChartsCoreOption): LegendItem[] {
	const value = option as WithLegend;
	const legend = legendOf(value);
	if (legend === undefined) return [];
	const series = seriesOf(value);
	const entries: LegendEntry[] =
		legend.data ?? series.flatMap((item) => (item.name === undefined ? [] : [item.name]));
	const items: LegendItem[] = [];
	for (const entry of entries) {
		const name = typeof entry === 'string' ? entry : entry.name;
		if (name === undefined) continue;
		const found = series.find((item) => item.name === name);
		items.push({
			name,
			color:
				found?.lineStyle?.color ??
				found?.itemStyle?.color ??
				found?.areaStyle?.color ??
				'var(--muted)',
			mark: markOf(found, typeof entry === 'string' ? undefined : entry.icon)
		});
	}
	return items;
}

/** Option mit `legend.selected` für alle Einträge: `false` für ausgeblendete Reihen */
export function withSelection(
	option: EChartsCoreOption,
	hidden: ReadonlySet<string>
): EChartsCoreOption {
	const items = legendItems(option);
	if (items.length === 0) return option;
	const value = option as WithLegend;
	const selected = Object.fromEntries(items.map((item) => [item.name, !hidden.has(item.name)]));
	const legend = legendOf(value);
	return { ...option, legend: { ...legend, selected } };
}
