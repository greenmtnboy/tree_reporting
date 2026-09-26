<script setup lang="ts">
import { computed, ref, watchEffect } from 'vue'
import FieldSketch from '../field-sketches/FieldSketch.vue'
import { fieldSketches, type FieldSketchName } from '../field-sketches'

const names = Object.keys(fieldSketches) as FieldSketchName[]
const requested = new URLSearchParams(location.search).get('sketch')
const selected = ref<FieldSketchName>(names.find(name => name === requested) ?? 'woodland')
const theme = ref<'light' | 'dark'>(matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
const guides = ref(false)
const panel = ref(true)
const visibility = ref(100)
const growthReplay = ref(0)
const sketch = computed(() => fieldSketches[selected.value])
const download = computed(() => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sketch.value.svg)}`)
const appOpacity = computed(() => selected.value === 'city' ? .24 : .4)

watchEffect(() => {
  document.documentElement.dataset.theme = theme.value
  const url = new URL(location.href)
  url.searchParams.set('sketch', selected.value)
  history.replaceState(null, '', url)
})
</script>

<template>
  <div class="studio">
    <header class="studio-header">
      <div>
        <p class="eyebrow">Urban Trees / Artwork</p>
        <h1>Field sketch studio</h1>
        <p class="intro">A closer look at the lines behind the landscape.</p>
      </div>
      <div class="theme-switch" role="group" aria-label="Preview theme">
        <button :aria-pressed="theme === 'light'" @click="theme = 'light'">Light</button>
        <button :aria-pressed="theme === 'dark'" @click="theme = 'dark'">Dark</button>
      </div>
    </header>

    <div class="studio-layout">
      <nav aria-label="Drawing collection">
        <p class="eyebrow">The collection</p>
        <button v-for="name in names" :key="name" class="collection-item"
          :aria-pressed="selected === name" @click="selected = name">
          <FieldSketch :name="name" class="thumbnail" />
          <span>{{ fieldSketches[name].title }}</span>
        </button>
        <p class="collection-note">Six generated SVGs.<br />The app uses these same files.</p>
      </nav>

      <main>
        <div class="drawing-heading">
          <div>
            <p class="eyebrow">First-pass study · ready for review</p>
            <h2>{{ sketch.title }}</h2>
            <p>{{ sketch.motif }} <span class="separator">/</span> {{ sketch.example }}</p>
          </div>
          <a :href="download" :download="`${selected}.svg`" class="download-link">Save SVG ↗</a>
        </div>

        <div class="controls">
          <label>Study visibility <input v-model.number="visibility" type="range" min="10" max="100" step="5" /> <output>{{ visibility }}%</output></label>
          <label><input v-model="guides" type="checkbox" /> Alignment grid</label>
          <label><input v-model="panel" type="checkbox" /> Panel over app preview</label>
          <button v-if="selected !== 'city'" class="replay-growth" @click="growthReplay++">Replay growth</button>
        </div>

        <div class="comparison">
          <section aria-label="Full drawing" class="study-card">
            <header><h3>Full drawing</h3><span>Uncropped · {{ visibility }}% visibility</span></header>
            <div class="canvas full-canvas" :class="{ 'with-grid': guides }">
              <FieldSketch :name="selected" class="full-study" :style="{ opacity: visibility / 100 }" />
            </div>
            <p class="caption">{{ selected === 'city' ? 'Check the shared ground plane, facade corners, and hidden edges here.' : 'Check leaf bases, branch junctions, and every line ending here.' }}</p>
          </section>
          <section aria-label="App placement" class="study-card">
            <header><h3>App placement</h3><span>{{ Math.round(appOpacity * 100) }}% visibility + edge crop</span></header>
            <div class="canvas context-canvas" :class="{ 'with-grid': guides, 'city-context': selected === 'city' }">
              <FieldSketch :key="`${selected}-${growthReplay}`" :name="selected" :grow="selected !== 'city'" class="context-study" :style="{ opacity: appOpacity }" />
              <div v-if="panel" class="panel-veil"></div>
              <div v-if="selected === 'city'" class="brand-preview">
                <strong>Urban Trees</strong>
                <span>The Concrete Jungle</span>
              </div>
              <div v-else class="context-label">
                <span class="eyebrow">Lower right</span>
                <p>{{ panel ? 'Under a translucent panel' : 'Background only' }}</p>
              </div>
            </div>
            <p class="caption">Cropping and the panel soften the drawing. They do not join loose paths.</p>
          </section>
        </div>

        <aside class="review-note">
          <p class="eyebrow">Construction rules</p>
          <p>{{ sketch.review }}</p>
          <code>src/src/artwork/field-sketches/{{ selected }}.svg</code>
        </aside>
        <p class="footnote">These are decorative ecosystem studies, not species-identification illustrations. Edit a generator and run pnpm artwork:generate to update both previews and the app.</p>
      </main>
    </div>
  </div>
</template>

<style>
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--color-ink); font-family: var(--font-body); }
button, input { font: inherit; }
button, a { -webkit-tap-highlight-color: transparent; }
button { cursor: pointer; }
button:focus-visible, a:focus-visible, input:focus-visible { outline: 2px solid var(--color-leaf); outline-offset: 4px; }
p, h1, h2, h3 { margin: 0; }
.studio { max-width: 1600px; margin: auto; padding: 28px 32px 40px; }
.studio-header { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding-bottom: 26px; border-bottom: 1px solid var(--color-border); }
.eyebrow { font-size: .64rem; font-weight: 600; letter-spacing: .13em; text-transform: uppercase; color: var(--color-muted); }
h1 { font: 400 2.35rem/1.2 var(--font-display); letter-spacing: -.035em; margin: 8px 0; }
.intro, .drawing-heading p, .collection-note { font-size: .8rem; line-height: 1.6; color: var(--color-muted); }
.theme-switch { display: flex; padding: 4px; border: 1px solid var(--color-border); border-radius: 12px; }
.theme-switch button { border: 0; background: transparent; color: var(--color-muted); padding: 9px 18px; border-radius: 8px; }
.theme-switch button[aria-pressed='true'] { background: var(--accent-soft); color: var(--color-leaf); }
.studio-layout { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 30px; padding-top: 28px; }
nav > .eyebrow { margin-bottom: 14px; }
.collection-item { display: flex; align-items: center; gap: 10px; width: 100%; margin-bottom: 7px; padding: 8px; border: 1px solid transparent; border-radius: 10px; background: transparent; color: var(--color-muted); text-align: left; font-size: .78rem; }
.collection-item:hover { background: var(--surface-2); }
.collection-item[aria-pressed='true'] { border-color: var(--color-border); background: var(--accent-soft); color: var(--color-leaf); }
.thumbnail { width: 35px; flex-shrink: 0; }
.collection-note { margin: 22px 8px; font-size: .72rem; }
.drawing-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
h2 { font: 400 1.8rem/1.3 var(--font-display); margin: 5px 0; }
.drawing-heading .eyebrow { font-size: .64rem; }
.separator { padding: 0 8px; opacity: .4; }
.download-link { white-space: nowrap; border-bottom: 1px solid var(--color-border); padding: 6px 0; color: var(--color-leaf); font-size: .75rem; text-decoration: none; }
.controls { display: flex; align-items: center; flex-wrap: wrap; gap: 14px 22px; padding: 20px 0 16px; font-size: .72rem; color: var(--color-muted); }
.controls label { display: flex; align-items: center; gap: 8px; }
.replay-growth { border: 1px solid var(--color-border); border-radius: 6px; padding: 5px 9px; color: var(--color-leaf); background: var(--surface-1); }
input { accent-color: var(--color-leaf); }
input[type='range'] { width: 85px; }
output { min-width: 3ch; font-variant-numeric: tabular-nums; }
.comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.study-card { min-width: 0; }
.study-card header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
h3 { font-size: .75rem; font-weight: 600; }
.study-card header span { font-size: .65rem; color: var(--color-muted); }
.canvas { position: relative; height: 385px; overflow: hidden; border: 1px solid var(--color-border); border-radius: 12px; background: var(--surface-1); color: var(--sketch-leaf); }
.full-canvas { display: grid; place-items: center; padding: 18px; }
.full-study { width: min(100%, 312px); }
.full-study[data-sketch='city'] { color: var(--sketch-city); width: 100%; }
.with-grid { background-image: linear-gradient(rgba(var(--accent-rgb), .1) 1px, transparent 1px), linear-gradient(90deg, rgba(var(--accent-rgb), .1) 1px, transparent 1px); background-size: 30px 30px; }
.context-study { position: absolute; width: 510px; bottom: -32px; right: -35px; }
.city-context .context-study { width: 230px; top: -4px; left: 44px; bottom: auto; right: auto; color: var(--sketch-city); clip-path: inset(4px 14px 26px 0); }
.brand-preview { position: absolute; top: 0; left: 0; width: 260px; padding: 28px 22px 22px; border-right: 1px solid var(--color-border); border-bottom: 1px solid var(--color-border); }
.brand-preview strong { display: block; font: 400 1.85rem var(--font-display); letter-spacing: -.035em; }
.brand-preview span { display: block; margin-top: 6px; font-size: .76rem; color: var(--color-muted); }
.panel-veil { position: absolute; inset: 0; background: rgba(var(--surface-rgb), .62); }
.context-label { position: absolute; top: 20px; left: 20px; right: 20px; }
.context-label p { font: 400 1.05rem/1.4 var(--font-display); margin-top: 6px; }
.caption { font-size: .7rem; line-height: 1.5; color: var(--color-muted); margin-top: 9px; }
.review-note { margin-top: 20px; border-left: 2px solid var(--color-leaf); padding: 4px 0 4px 16px; }
.review-note > p:not(.eyebrow) { margin: 8px 0; font-size: .8rem; line-height: 1.55; max-width: 80ch; }
.review-note code { color: var(--color-muted); font-size: .68rem; overflow-wrap: anywhere; }
.footnote { margin-top: 20px; color: var(--color-muted); font-size: .7rem; line-height: 1.6; }
@media (max-width: 1000px) { .studio { padding: 20px; } .studio-layout { grid-template-columns: 150px minmax(0, 1fr); gap: 20px; } .comparison { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .studio-header { align-items: flex-start; } h1 { font-size: 1.7rem; } .intro { max-width: 24ch; } .theme-switch button { padding: 9px; } .studio-layout { display: block; } nav { display: flex; overflow-x: auto; gap: 6px; margin-bottom: 24px; } nav > p { display: none; } .collection-item { min-width: 145px; } .drawing-heading { align-items: flex-start; } h2 { font-size: 1.45rem; } .canvas { height: 360px; } }
</style>
