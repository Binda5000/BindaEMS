<svelte:options namespace="svg" />

<script lang="ts">
	import type { IconName } from '../nav';
	import { ICONS } from './icons';

	interface Props {
		name: IconName;
	}

	let { name }: Props = $props();
</script>

{#each ICONS[name] as shape, index (index)}
	{#if 'd' in shape}
		<path d={shape.d} fill={shape.fill ? 'currentColor' : 'none'} />
	{:else if 'cx' in shape}
		<circle cx={shape.cx} cy={shape.cy} r={shape.r} />
	{:else}
		<rect x={shape.x} y={shape.y} width={shape.width} height={shape.height} rx={shape.rx ?? 0} />
	{/if}
{/each}
