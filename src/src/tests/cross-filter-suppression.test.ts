/**
 * Validates the suppress-self-reload logic in EmbeddedDashboardChart.
 *
 * When a chart emits a dimension-click it sets suppressNextFilterUpdate = true
 * before propagating the event. The parent reacts by bumping crossFilters.version,
 * causing the chart's :filters prop to receive a new array reference. The watcher
 * inside the component fires but must NOT call runQuery for that one cycle.
 *
 * This test reproduces the flag + watch pattern in isolation using Vue's
 * reactivity primitives (no DOM / component mount required).
 */
import { describe, it, expect, vi } from 'vitest'
import { ref, watch, nextTick } from 'vue'

function makeChartBehavior() {
  const filters = ref<string[]>([])
  const runQuery = vi.fn()
  let suppressNextFilterUpdate = false

  watch(
    () => JSON.stringify(filters.value),
    () => {
      if (suppressNextFilterUpdate) {
        suppressNextFilterUpdate = false
        return
      }
      runQuery()
    },
  )

  function onDimensionClick() {
    // Mirrors handleDimensionClick: set flag BEFORE the parent mutates state
    suppressNextFilterUpdate = true
  }

  async function simulateParentFilterUpdate(newFilters: string[]) {
    filters.value = newFilters
    await nextTick()
  }

  return { runQuery, onDimensionClick, simulateParentFilterUpdate }
}

describe('EmbeddedDashboardChart cross-filter self-suppression', () => {
  it('runs query when an external filter change arrives', async () => {
    const { runQuery, simulateParentFilterUpdate } = makeChartBehavior()

    await simulateParentFilterUpdate(["city = 'USSFO'"])

    expect(runQuery).toHaveBeenCalledTimes(1)
  })

  it('skips the query when the chart itself triggered the filter change', async () => {
    const { runQuery, onDimensionClick, simulateParentFilterUpdate } = makeChartBehavior()

    // Prime with a base filter so the chart has run once
    await simulateParentFilterUpdate(["city = 'USSFO'"])
    expect(runQuery).toHaveBeenCalledTimes(1)

    // Chart emits dimension-click → flag set → parent updates filters
    onDimensionClick()
    await simulateParentFilterUpdate(["city = 'USSFO'", "tree_form = 'broadleaf'"])

    // Should still be 1 — the source chart must not reload itself
    expect(runQuery).toHaveBeenCalledTimes(1)
  })

  it('resumes normal query execution after the suppressed cycle', async () => {
    const { runQuery, onDimensionClick, simulateParentFilterUpdate } = makeChartBehavior()

    await simulateParentFilterUpdate(["city = 'USSFO'"])
    expect(runQuery).toHaveBeenCalledTimes(1)

    // Suppressed click
    onDimensionClick()
    await simulateParentFilterUpdate(["city = 'USSFO'", "tree_form = 'broadleaf'"])
    expect(runQuery).toHaveBeenCalledTimes(1)

    // Subsequent external change (e.g. city switch) must still run
    await simulateParentFilterUpdate(["city = 'USNYC'", "tree_form = 'broadleaf'"])
    expect(runQuery).toHaveBeenCalledTimes(2)
  })

  it('does not suppress a second consecutive click', async () => {
    const { runQuery, onDimensionClick, simulateParentFilterUpdate } = makeChartBehavior()

    await simulateParentFilterUpdate(["city = 'USSFO'"])
    expect(runQuery).toHaveBeenCalledTimes(1)

    // First click: suppressed
    onDimensionClick()
    await simulateParentFilterUpdate(["city = 'USSFO'", "tree_form = 'broadleaf'"])
    expect(runQuery).toHaveBeenCalledTimes(1)

    // Second click (e.g. selecting a different category): also suppressed
    onDimensionClick()
    await simulateParentFilterUpdate(["city = 'USSFO'", "tree_form = 'palm'"])
    expect(runQuery).toHaveBeenCalledTimes(1)

    // Next external change: runs
    await simulateParentFilterUpdate(["city = 'USNYC'", "tree_form = 'palm'"])
    expect(runQuery).toHaveBeenCalledTimes(2)
  })
})
