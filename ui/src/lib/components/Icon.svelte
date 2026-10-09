<script lang="ts">
	import type { IconName } from '../nav';
	import IconShapes from './IconShapes.svelte';

	interface Props {
		name: IconName;
	}

	let { name }: Props = $props();
</script>

<svg
	class="icon"
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.75"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
	focusable="false"
>
	<IconShapes {name} />
</svg>

<style>
	.icon {
		width: 1.25em;
		height: 1.25em;
		flex: none;
	}
</style>
