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
          <ul>
            <li><a href="https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq" target="_blank" rel="noopener">SF Open Data Portal — Street Trees</a></li>
            <li><a href="https://data.cityofnewyork.us/Environment/2015-Street-Tree-Census-Tree-Data/uvpi-gqnh" target="_blank" rel="noopener">NYC Open Data — Street Tree Census</a></li>
            <li><a href="https://data.boston.gov/dataset/bprd-trees" target="_blank" rel="noopener">City of Boston — Street Trees</a></li>
          </ul>
        </div>

        <div class="source-card">
          <h3>Species Enrichment</h3>
          <p>
            Per-species attributes (native status, evergreen, mature height, canopy
            spread, growth rate, lifespan, drought tolerance, bloom season, wildlife
            value, fire risk) are aggregated from the following. Inaccuracies may exist - corrections welcome! 
          </p>
          <ul>
            <li>
              <a href="https://en.wikipedia.org/" target="_blank" rel="noopener">Wikipedia</a>
              &mdash; REST &amp; MediaWiki APIs
            </li>
            <li>
              <a href="https://powo.science.kew.org/" target="_blank" rel="noopener">Plants of the World Online</a>
              &mdash; POWO / Royal Botanic Gardens, Kew
            </li>
            <li>
              <a href="https://www.gbif.org/" target="_blank" rel="noopener">GBIF</a>
              &mdash; Global Biodiversity Information Facility
            </li>
            <li>
              <a href="https://selectree.calpoly.edu/" target="_blank" rel="noopener">SelecTree</a>
              &mdash; Cal Poly Urban Forest Ecosystems Institute
            </li>
          </ul>
        </div>

        <div class="source-card">
          <h3>Landmarks</h3>
          <p>
            SF Open Data Portal &mdash; Landmarks dataset (SF); curated lists for NYC and Boston.
          </p>
          <a
            href="https://data.sfgov.org/Geographic-Locations-and-Boundaries/Landmarks/rzic-39gi/about_data"
            target="_blank"
            rel="noopener"
          >
            data.sfgov.org &rarr;
          </a>
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

<style scoped>
.info-page {
  height: 100%;
  overflow-y: auto;
  background: #1a1a2e;
  -webkit-overflow-scrolling: touch;
}

.info-content {
  max-width: 640px;
  margin: 0 auto;
  padding: 32px 24px 48px;
}

h1 {
  font-size: 1.5rem;
  font-weight: 700;
  color: #e0e0e0;
  margin-bottom: 8px;
}

.info-lead {
  color: #7a7a9e;
  font-size: 0.95rem;
  line-height: 1.6;
  margin-bottom: 32px;
}

h2 {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #7a7a9e;
  margin-bottom: 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid #0f3460;
}

section {
  margin-bottom: 28px;
}

.source-card {
  background: #16213e;
  border: 1px solid #0f3460;
  border-radius: 6px;
  padding: 14px 16px;
  margin-bottom: 10px;
}

.source-card h3 {
  font-size: 0.9rem;
  font-weight: 600;
  color: #e0e0e0;
  margin-bottom: 6px;
}

.source-card p {
  color: #a0a0c0;
  font-size: 0.85rem;
  line-height: 1.5;
  margin-bottom: 6px;
}

.source-card p:last-child {
  margin-bottom: 0;
}

.source-card a {
  color: #4fc3f7;
  text-decoration: none;
  font-size: 0.85rem;
  transition: color 0.15s;
}

.source-card a:hover {
  color: #81d4fa;
}

.source-card ul {
  list-style: none;
  padding: 0;
  margin: 8px 0;
}

.source-card ul li {
  padding: 4px 0 4px 12px;
  position: relative;
  color: #a0a0c0;
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
  background: #4fc3f7;
}

.source-card ul li a {
  color: #4fc3f7;
  text-decoration: none;
}

.source-card ul li a:hover {
  color: #81d4fa;
}

.source-note {
  font-size: 0.8rem !important;
  color: #555577 !important;
  font-style: italic;
}

.tech-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.tech-item {
  background: #16213e;
  border: 1px solid #0f3460;
  border-radius: 6px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tech-label {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555577;
}

.tech-item span:last-child {
  color: #e0e0e0;
  font-size: 0.85rem;
}

.repo-link {
  color: #4fc3f7;
  text-decoration: none;
  font-size: 0.9rem;
  transition: color 0.15s;
}

.repo-link:hover {
  color: #81d4fa;
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
