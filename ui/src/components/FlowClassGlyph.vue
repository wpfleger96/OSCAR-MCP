<template>
    <svg
        :width="width"
        :height="height"
        viewBox="0 0 60 30"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
    >
        <path v-if="path" :d="path" />
    </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

// Coordinate system: viewBox 0 0 60 30, baseline y=27, plateau level y≈10,
// peak apex y≈2, x spans one inspiration (0=start, 60=end). New classes
// should be authored within these bounds to render consistently at all sizes.
const GLYPH_PATHS: Record<number, string> = {
    // Class 1: sinusoidal — smooth, symmetric bell
    1: 'M 0 27 C 15 2 45 2 60 27',
    // Class 2: two peaks — valley tuned to ~mid-height (y≈15)
    2: 'M 0 27 C 10 3 17 3 22 12 C 26 17 34 17 38 12 C 43 3 50 3 60 27',
    // Class 3: multiple tiny ripples along a plateau band
    3: 'M 0 27 C 6 8 8 8 12 10 C 15 12 17 8 21 10 C 24 12 26 8 30 10 C 33 12 35 8 39 10 C 43 8 54 8 60 27',
    // Class 4: clearly taller initial peak, then flat plateau
    4: 'M 0 27 C 8 2 14 2 18 10 L 50 10 C 54 10 58 18 60 27',
    // Class 5: plateau, central bump clearly above plateau, plateau
    5: 'M 0 27 C 4 10 6 10 10 10 L 22 10 C 26 2 34 2 38 10 L 50 10 C 54 10 58 18 60 27',
    // Class 6: long plateau, then late-phase peak
    6: 'M 0 27 C 4 10 6 10 10 10 L 38 10 C 44 2 52 2 60 27',
    // Class 7: flat plateau throughout, no bump
    7: 'M 0 27 C 4 10 6 10 10 10 L 50 10 C 54 10 58 18 60 27',
}

const props = withDefaults(
    defineProps<{
        classNum: number
        size?: 'sm' | 'lg'
    }>(),
    { size: 'sm' },
)

const path = computed(() => GLYPH_PATHS[props.classNum] ?? null)
const width = computed(() => (props.size === 'lg' ? 120 : 40))
const height = computed(() => (props.size === 'lg' ? 60 : 20))
</script>
