<template>
  <div class="hbar-chart" role="list">
    <div
      v-for="item in data"
      :key="item.label"
      class="hbar-row"
      :class="{ 'hbar-row--active': selected === item.label, 'hbar-row--dimmed': selected && selected !== item.label }"
      role="listitem"
      tabindex="0"
      @click="$emit('select', item.label)"
      @keydown.enter="$emit('select', item.label)"
    >
      <div class="hbar-label" :title="item.label">{{ item.label }}</div>
      <div class="hbar-track">
        <div
          class="hbar-fill"
          :style="{ width: `${pct(item.value)}%`, background: item.color || '#4fc3f7' }"
        />
      </div>
      <div class="hbar-value">{{ fmt(item.value) }}</div>
    </div>
    <div v-if="data.length === 0" class="hbar-empty">No data</div>
  </div>
</template>

<script setup lang="ts">
export interface HBarItem {
  label: string
  value: number
  color?: string
}

const props = defineProps<{
  data: HBarItem[]
  selected?: string | null
}>()

defineEmits<{ select: [label: string] }>()

const max = () => Math.max(...props.data.map((d) => d.value), 1)
const pct = (v: number) => Math.round((v / max()) * 100)
const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
</script>

<style scoped>
.hbar-chart {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.hbar-row {
  display: grid;
  grid-template-columns: 160px 1fr 52px;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  border-radius: 4px;
  padding: 3px 4px;
  transition: background 0.12s;
}

.hbar-row:hover {
  background: rgba(79, 195, 247, 0.07);
}

.hbar-row--active {
  background: rgba(79, 195, 247, 0.12);
}

.hbar-row--dimmed {
  opacity: 0.38;
}

.hbar-label {
  font-size: 0.78rem;
  color: #c0c0d8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: right;
  padding-right: 4px;
}

.hbar-track {
  height: 18px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  overflow: hidden;
}

.hbar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.35s ease;
}

.hbar-value {
  font-size: 0.72rem;
  color: #7a7a9e;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.hbar-empty {
  font-size: 0.8rem;
  color: #555577;
  font-style: italic;
  padding: 12px 4px;
}
</style>
