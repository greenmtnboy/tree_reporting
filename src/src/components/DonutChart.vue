<template>
  <div class="donut-wrap">
    <svg :viewBox="`0 0 ${SIZE} ${SIZE}`" class="donut-svg" role="img">
      <g :transform="`translate(${SIZE / 2}, ${SIZE / 2})`">
        <circle :r="INNER_R" class="donut-hole" />
        <path
          v-for="(seg, i) in segments"
          :key="i"
          :d="seg.d"
          :fill="seg.color"
          :opacity="selected && selected !== seg.label ? 0.3 : 1"
          class="donut-seg"
          @click="$emit('select', seg.label)"
        />
      </g>
      <!-- Center label -->
      <text :x="SIZE / 2" :y="SIZE / 2 - 6" text-anchor="middle" class="donut-center-num">
        {{ fmt(total) }}
      </text>
      <text :x="SIZE / 2" :y="SIZE / 2 + 14" text-anchor="middle" class="donut-center-label">
        {{ selected || 'total' }}
      </text>
    </svg>

    <div class="donut-legend">
      <div
        v-for="seg in segments"
        :key="seg.label"
        class="legend-item"
        :class="{ 'legend-item--active': selected === seg.label, 'legend-item--dimmed': selected && selected !== seg.label }"
        @click="$emit('select', seg.label)"
      >
        <span class="legend-dot" :style="{ background: seg.color }" />
        <span class="legend-label">{{ seg.label }}</span>
        <span class="legend-pct">{{ Math.round((seg.value / total) * 100) }}%</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface DonutItem {
  label: string
  value: number
  color: string
}

const props = defineProps<{
  data: DonutItem[]
  selected?: string | null
}>()

defineEmits<{ select: [label: string] }>()

const SIZE = 200
const OUTER_R = 88
const INNER_R = 54
const GAP = 0.018  // radians gap between slices

const total = computed(() => props.data.reduce((s, d) => s + d.value, 0))

const segments = computed(() => {
  const t = total.value
  if (t === 0) return []
  let angle = -Math.PI / 2
  return props.data.map((d) => {
    const sweep = (d.value / t) * Math.PI * 2 - GAP
    const startAngle = angle + GAP / 2
    const endAngle = startAngle + sweep
    const x1 = Math.cos(startAngle) * OUTER_R
    const y1 = Math.sin(startAngle) * OUTER_R
    const x2 = Math.cos(endAngle) * OUTER_R
    const y2 = Math.sin(endAngle) * OUTER_R
    const ix1 = Math.cos(endAngle) * INNER_R
    const iy1 = Math.sin(endAngle) * INNER_R
    const ix2 = Math.cos(startAngle) * INNER_R
    const iy2 = Math.sin(startAngle) * INNER_R
    const large = sweep > Math.PI ? 1 : 0
    const pathD = [
      `M ${x1} ${y1}`,
      `A ${OUTER_R} ${OUTER_R} 0 ${large} 1 ${x2} ${y2}`,
      `L ${ix1} ${iy1}`,
      `A ${INNER_R} ${INNER_R} 0 ${large} 0 ${ix2} ${iy2}`,
      'Z',
    ].join(' ')
    angle += (d.value / t) * Math.PI * 2
    return { label: d.label, value: d.value, color: d.color, d: pathD }
  })
})

function fmt(v: number) {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1000) return `${(v / 1000).toFixed(0)}k`
  return String(v)
}
</script>

<style scoped>
.donut-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  width: 100%;
}

.donut-svg {
  width: 180px;
  height: 180px;
  overflow: visible;
}

.donut-hole {
  fill: #1a1a2e;
}

.donut-seg {
  cursor: pointer;
  transition: opacity 0.2s;
  stroke: #1a1a2e;
  stroke-width: 1;
}

.donut-seg:hover {
  filter: brightness(1.15);
}

.donut-center-num {
  fill: #e0e0e0;
  font-size: 22px;
  font-weight: 700;
}

.donut-center-label {
  fill: #7a7a9e;
  font-size: 11px;
  text-transform: capitalize;
}

.donut-legend {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  max-width: 240px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 4px;
  transition: background 0.12s, opacity 0.2s;
  font-size: 0.78rem;
}

.legend-item:hover {
  background: rgba(79, 195, 247, 0.07);
}

.legend-item--active {
  background: rgba(79, 195, 247, 0.1);
}

.legend-item--dimmed {
  opacity: 0.35;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}

.legend-label {
  flex: 1;
  color: #c0c0d8;
  text-transform: capitalize;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legend-pct {
  color: #7a7a9e;
  font-variant-numeric: tabular-nums;
}
</style>
