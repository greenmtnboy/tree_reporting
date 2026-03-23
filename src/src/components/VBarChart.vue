<template>
  <div class="vbar-chart" role="list">
    <div class="vbar-bars">
      <div
        v-for="item in data"
        :key="item.label"
        class="vbar-col"
        :title="`${item.label}: ${item.value.toLocaleString()}`"
        role="listitem"
      >
        <div class="vbar-value-label">{{ fmt(item.value) }}</div>
        <div class="vbar-track">
          <div
            class="vbar-fill"
            :style="{ height: `${pct(item.value)}%`, background: item.color || '#4fc3f7' }"
          />
        </div>
        <div class="vbar-label">{{ item.label }}</div>
      </div>
    </div>
    <div v-if="data.length === 0" class="vbar-empty">No data</div>
  </div>
</template>

<script setup lang="ts">
export interface VBarItem {
  label: string
  value: number
  color?: string
}

const props = defineProps<{
  data: VBarItem[]
}>()

const max = () => Math.max(...props.data.map((d) => d.value), 1)
const pct = (v: number) => Math.round((v / max()) * 100)
function fmt(v: number) {
  if (v >= 1000) return `${(v / 1000).toFixed(0)}k`
  return String(v)
}
</script>

<style scoped>
.vbar-chart {
  width: 100%;
  height: 100%;
}

.vbar-bars {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 180px;
  width: 100%;
  overflow-x: auto;
  padding-bottom: 2px;
}

.vbar-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  min-width: 28px;
  max-width: 60px;
  height: 100%;
}

.vbar-value-label {
  font-size: 0.6rem;
  color: #7a7a9e;
  margin-bottom: 2px;
  font-variant-numeric: tabular-nums;
}

.vbar-track {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 3px 3px 0 0;
  overflow: hidden;
}

.vbar-fill {
  width: 100%;
  border-radius: 3px 3px 0 0;
  transition: height 0.35s ease;
}

.vbar-label {
  font-size: 0.62rem;
  color: #7a7a9e;
  margin-top: 4px;
  text-align: center;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.vbar-empty {
  font-size: 0.8rem;
  color: #555577;
  font-style: italic;
  padding: 12px 4px;
}
</style>
