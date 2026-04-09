<template>
  <div class="info-page">
    <div class="info-content">
      <h1>About Urban Trees</h1>
      <p class="info-lead">
        An interactive browser-based map of urban forests across multiple cities. Explore
        hundreds of thousands of street trees by location, species, and ecological
        attributes.
      </p>

      <section>
        <h2>Data Sources</h2>

        <div class="source-card">
          <h3>Tree Inventory</h3>
          <p>Street tree inventory data from open data portals for each live city.</p>
          <details class="city-details">
            <summary class="city-summary">View by city</summary>
            <ul>
              <li v-for="source in treeInventorySources" :key="source.city">
                <a v-if="source.url" :href="source.url" target="_blank" rel="noopener">
                  {{ source.city }} - {{ source.label }}
                </a>
                <template v-else>{{ source.city }} - {{ source.label }}</template>
              </li>
            </ul>
          </details>
        </div>

        <div class="source-card">
          <h3>Species Enrichment</h3>
          <p>
            Per-species attributes (native status, evergreen, mature height, canopy
            spread, growth rate, lifespan, drought tolerance, bloom season, wildlife
            value, fire risk) are aggregated from the following. Inaccuracies may exist -
            corrections welcome!
          </p>
          <ul>
            <li v-for="source in speciesEnrichmentSources" :key="source.label">
              <a :href="source.url" target="_blank" rel="noopener">{{ source.label }}</a>
              &mdash; {{ source.description }}
            </li>
          </ul>
        </div>

        <div class="source-card">
          <h3>Landmarks</h3>
          <p>Points of interest sourced from open data portals and preservation registries.</p>
          <details class="city-details">
            <summary class="city-summary">View by city</summary>
            <ul>
              <li v-for="source in landmarkSources" :key="source.city">
                <a v-if="source.url" :href="source.url" target="_blank" rel="noopener">
                  {{ source.city }} - {{ source.label }}
                </a>
                <template v-else>{{ source.city }} - {{ source.label }}</template>
              </li>
            </ul>
          </details>
        </div>

        <div class="source-card">
          <h3>Basemap</h3>
          <p>
            <a href="https://carto.com/basemaps" target="_blank" rel="noopener">CARTO</a>
            Dark Matter vector tile style.
          </p>
        </div>
      </section>

      <section>
        <h2>Tech Stack</h2>
        <div class="tech-grid">
          <div class="tech-item"><span class="tech-label">Framework</span><span>Vue 3</span></div>
          <div class="tech-item"><span class="tech-label">Build</span><span>Vite 6</span></div>
          <div class="tech-item"><span class="tech-label">Map</span><span>MapLibre GL 4</span></div>
          <div class="tech-item"><span class="tech-label">SQL</span><span>DuckDB WASM</span></div>
          <div class="tech-item"><span class="tech-label">Query</span><span>Trilogy</span></div>
        </div>
      </section>

      <section>
        <h2>See Also</h2>
        <div class="source-card">
          <a href="https://bsm.sfdpw.org/urbanforestry/" target="_blank" rel="noopener">
            SF Urban Forestry &rarr;
          </a>
          <p>San Francisco Department of Public Works Urban Forestry division.</p>
        </div>
        <div class="source-card">
          <a href="https://greenmtnboy.github.io/space_reporting/" target="_blank" rel="noopener">
            Want something different? How about rockets visualized? &rarr;
          </a>
          <p>An interactive reporting view for space data.</p>
        </div>
      </section>

      <section class="source-section">
        <h2>Source Code</h2>
        <a
          href="https://github.com/greenmtnboy/sf_tree_reporting"
          target="_blank"
          rel="noopener"
          class="repo-link"
        >
          github.com/greenmtnboy/sf_tree_reporting &rarr;
        </a>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  LANDMARK_SOURCES,
  SPECIES_ENRICHMENT_SOURCES,
  TREE_INVENTORY_SOURCES,
} from '../data/sourceCatalog'

const treeInventorySources = TREE_INVENTORY_SOURCES
const speciesEnrichmentSources = SPECIES_ENRICHMENT_SOURCES
const landmarkSources = LANDMARK_SOURCES
</script>

<style scoped>
.info-page {
  height: 100%;
  overflow-y: auto;
  background: transparent;
  -webkit-overflow-scrolling: touch;
}

.info-content {
  max-width: 640px;
  margin: 0 auto;
  padding: 32px 24px 48px;
}

h1 {
  font-size: 1.8rem;
  font-weight: 700;
  font-family: var(--font-display);
  color: var(--color-ink);
  margin-bottom: 8px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.info-lead {
  color: rgba(237, 242, 235, 0.76);
  font-size: 0.95rem;
  line-height: 1.6;
  margin-bottom: 32px;
}

h2 {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  color: var(--color-moss);
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
}

section {
  margin-bottom: 28px;
}

.source-card {
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.82), rgba(28, 31, 36, 0.96));
  border: 1px solid rgba(167, 227, 178, 0.1);
  padding: 14px 16px;
  margin-bottom: 10px;
  box-shadow: var(--shadow-soft);
}

.source-card h3 {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-ink);
  margin-bottom: 6px;
}

.source-card p {
  color: rgba(237, 242, 235, 0.72);
  font-size: 0.85rem;
  line-height: 1.5;
  margin-bottom: 6px;
}

.source-card p:last-child {
  margin-bottom: 0;
}

.source-card a {
  color: var(--color-leaf);
  text-decoration: none;
  font-size: 0.85rem;
  transition: color 0.15s;
}

.source-card a:hover {
  color: #d4f4d8;
}

.source-card ul {
  list-style: none;
  padding: 0;
  margin: 8px 0;
}

.source-card ul li {
  padding: 4px 0 4px 12px;
  position: relative;
  color: rgba(237, 242, 235, 0.72);
  font-size: 0.85rem;
  line-height: 1.5;
}

.source-card ul li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 12px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-moss);
}

.source-card ul li a {
  color: var(--color-leaf);
  text-decoration: none;
}

.source-card ul li a:hover {
  color: #d4f4d8;
}

.city-details {
  margin-top: 8px;
}

.city-summary {
  font-size: 0.8rem;
  color: var(--color-leaf);
  cursor: pointer;
  user-select: none;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 4px;
}

.city-summary::-webkit-details-marker {
  display: none;
}

.city-summary::before {
  content: '>';
  font-size: 0.6rem;
  transition: transform 0.15s;
  display: inline-block;
}

details[open] > .city-summary::before {
  transform: rotate(90deg);
}

.city-summary:hover {
  color: #d4f4d8;
}

.source-note {
  font-size: 0.8rem !important;
  color: rgba(154, 166, 154, 0.72) !important;
  font-style: italic;
}

.tech-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.tech-item {
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.82), rgba(28, 31, 36, 0.96));
  border: 1px solid rgba(167, 227, 178, 0.1);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tech-label {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: rgba(154, 166, 154, 0.74);
}

.tech-item span:last-child {
  color: var(--color-ink);
  font-size: 0.85rem;
}

.repo-link {
  color: var(--color-leaf);
  text-decoration: none;
  font-size: 0.9rem;
  transition: color 0.15s;
}

.repo-link:hover {
  color: #d4f4d8;
}

.source-section {
  margin-bottom: 0;
}

@media (max-width: 768px) {
  .info-content {
    padding: 20px 16px 32px;
  }

  h1 {
    font-size: 1.3rem;
  }

  .tech-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
