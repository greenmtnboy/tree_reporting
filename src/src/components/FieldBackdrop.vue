<script setup lang="ts">
import { computed } from 'vue'
import { getCityBiome } from '../composables/dashboardContextSource'
import { useMapData } from '../composables/useMapData'

const { displayCity } = useMapData()
const ecosystem = computed(() => {
  const biome = getCityBiome(displayCity.value).toLowerCase()
  if (biome.includes('conifer')) return 'conifer'
  if (biome.includes('mediterranean')) return 'woodland'
  if (biome.includes('desert')) return 'desert'
  if (biome.includes('grassland')) return 'grassland'
  return 'broadleaf'
})
</script>

<template>
  <div class="field-backdrop" aria-hidden="true" :data-ecosystem="ecosystem">
    <!-- Architectural pencil study, adapted from the Arborary homepage. -->
    <svg class="city-study" viewBox="0 0 520 360" fill="none">
      <g class="survey-lines">
        <path d="M-40 298 420 32M-40 338 480 38M-20 380 530 62M-10 245 280 412M36 215 380 414M94 182 520 428M150 149 560 385" />
        <path d="M18 38h85m-68-16v40M344 88h110m-18-17v40" />
        <circle cx="436" cy="88" r="18" />
      </g>
      <g class="city-buildings" transform="translate(30 50)">
        <path class="sketch-wash" d="m16 186 0-75 56-32 38 22v72l32-18V65l53-31 43 25v81l29-17V87l42-24 51 29v90l-175 102Z" />
        <path d="m16 186 0-75 56-32 38 22v72M16 111l39 23 55-33M55 134v74M72 79v-8l38 22v8M142 225V65l53-31 43 25v164M142 65l43 25 53-31M185 90v163M195 34V16m-8 5h16M267 237V87l42-24 51 29v90M267 87l51 29 42-24M318 116v96M-4 199l189 110 194-113" />
        <path class="pencil-detail" d="m26 130 18 10v15l-18-10Zm0 33 18 10v15l-18-10Zm40-21 14-8v14l-14 8Zm20-12 14-8v14l-14 8Zm-20 41 14-8v14l-14 8Zm20-12 14-8v14l-14 8ZM152 85l22 13v18l-22-13Zm0 38 22 13v18l-22-13Zm0 38 22 13v18l-22-13Zm43-62 13-8v17l-13 8Zm22-13 12-7v17l-12 7Zm-22 48 13-8v17l-13 8Zm22-13 12-7v17l-12 7Zm-22 49 13-8v17l-13 8Zm22-13 12-7v17l-12 7Zm61-51 30 17v17l-30-17Zm0 34 30 17v17l-30-17Zm49-15 23-13v19l-23 13Z" />
        <path class="pencil-detail" d="M13 187v-77l58-34M139 225V64l55-33M188 92v160M270 239V91M321 118v95M-7 204l192 111 199-115M118 196l15-9m-15 15 15-9m128 19 10-6" />
      </g>
    </svg>

    <!-- Decorative biome studies, not species-identification illustrations. -->
    <svg class="ecosystem-study" viewBox="0 0 460 510" fill="none">
      <g class="contours"><path d="M25 484c80-45 139-7 202-25s133-16 238-55M4 501c91-43 157-4 230-22s156-25 240-53M76 459c52-23 100 1 156-17s135-5 223-58" /></g>
      <g v-if="ecosystem === 'woodland'" class="botanical">
        <path d="M412 517Q286 361 235 127M302 363Q377 303 410 233M270 271Q187 249 124 186M331 410Q248 386 185 320" />
        <path class="sketch-wash" d="M241 160Q176 124 205 48Q254 91 241 160ZM255 215Q287 135 342 129Q325 199 255 215ZM270 271Q200 250 184 187Q256 198 270 271ZM291 332Q332 240 381 248Q370 312 291 332ZM331 410Q355 332 419 321Q405 396 331 410ZM218 352Q153 361 132 291Q196 291 218 352Z" />
        <path d="M241 160Q176 124 205 48Q254 91 241 160ZM255 215Q287 135 342 129Q325 199 255 215ZM270 271Q200 250 184 187Q256 198 270 271ZM291 332Q332 240 381 248Q370 312 291 332ZM331 410Q355 332 419 321Q405 396 331 410ZM218 352Q153 361 132 291Q196 291 218 352ZM205 48l36 112m14 55 87-86m-72 142-86-84m107 145 90-84m-50 162 88-89m-201 31-86-61M124 186q-36 3-42-39 42 5 42 39Zm286 47q-5-47 27-68 13 48-27 68Z" />
      </g>
      <g v-else-if="ecosystem === 'conifer'" class="botanical">
        <path d="M390 513Q278 331 199 96M279 314 119 252M301 358l114-147M246 237 326 102" />
        <path v-for="i in 10" :key="i" :d="`M${204+i*7} ${112+i*19}l-62-36m62 36 30-60`" />
        <path v-for="i in 7" :key="`left-${i}`" :d="`M${130+i*19} ${257+i*7}l-10-43m10 43-43 17M${406-i*13} ${223+i*17}l-32-12m32 12 15-38`" />
        <path d="M341 400c-49-8-58 44-22 72 45-5 60-51 22-72Zm-17 8 23 10-31 9 30 10-23 10 14 13M180 105l18-48 20 58" />
      </g>
      <g v-else-if="ecosystem === 'desert'" class="botanical">
        <path class="sketch-wash" d="M276 484V218c0-32 36-32 36 0v82h23v-72c0-25 27-25 27 0v88q0 22-24 22h-26v146Z" />
        <path d="M276 484V218c0-32 36-32 36 0v82h23v-72c0-25 27-25 27 0v88q0 22-24 22h-26v146M276 365h-35q-27 0-27-25v-59c0-24 27-24 27 0v50h35M290 475V217m8 250V223M227 288v51q0 12 30 12M349 237v75l-25 12M159 471q-14-85-62-109 63 10 76 60-7-85 17-123 16 65-2 130 34-57 77-58-49 33-64 98" />
        <path class="pencil-detail" d="m133 439 22 3m-11-30 17 5m31-63-4 20m18 52 14-10m69-159 13-5m-15 43 13-5m-13 49 13-5m-13 58 13-5" />
      </g>
      <g v-else-if="ecosystem === 'grassland'" class="botanical">
        <path d="M325 500Q275 335 303 130M316 466Q204 349 167 228M335 500Q394 342 393 230M308 450Q256 300 225 200M338 480Q352 353 340 290M283 460Q187 410 107 390" />
        <path v-for="i in 7" :key="i" :d="`M${303-i*1.2} ${139+i*16}q-34-13-26-36 26 8 26 36q31-18 26-39-26 12-26 39M${169+i*5} ${236+i*13}q-30-3-29-24 24-1 29 24M393 ${238+i*14}q-24-15-16-31 22 12 16 31q28-6 28-28-21 2-28 28`" />
      </g>
      <g v-else class="botanical">
        <path d="M392 513Q294 345 236 170M301 355l93-146M272 282 137 236M328 409 193 366" />
        <g transform="translate(149 16) rotate(-19 60 130) scale(1.2)">
          <path class="sketch-wash" d="M59 115C47 123 39 113 47 106C19 112 18 93 35 92C6 91 10 70 31 76C12 59 23 45 40 57C28 37 40 28 49 39C40 11 65 3 69 27C88 15 94 35 78 44C105 29 111 53 88 60C116 59 111 82 88 82C107 96 87 108 76 98C90 114 72 125 64 115Z" />
          <path d="M59 115C47 123 39 113 47 106C19 112 18 93 35 92C6 91 10 70 31 76C12 59 23 45 40 57C28 37 40 28 49 39C40 11 65 3 69 27C88 15 94 35 78 44C105 29 111 53 88 60C116 59 111 82 88 82C107 96 87 108 76 98C90 114 72 125 64 115ZM60 134Q64 87 60 26M62 104 42 99m20-14L30 81m31-16L38 55m24 47 23-7M62 78 91 72M61 56 84 43" />
        </g>
        <path d="M394 209q-55 17-55-39c-37 0-39-38-12-43-18-31 9-53 29-32 10-38 51-33 47 2 36-19 54 16 27 37 41 16 29 50-6 46 7 22-11 35-30 29ZM301 355l93-146 1-98M374 240l-29-71m39 54 36-61M137 236Q57 253 62 172q64-4 75 64ZM193 366q-80 26-85-58 69-9 85 58ZM137 236l-60-49m116 179-69-43" />
      </g>
    </svg>
  </div>
</template>

<style scoped>
.field-backdrop { position: fixed !important; inset: 0; z-index: 0 !important; pointer-events: none; overflow: hidden; }
svg { position: absolute; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.15; }
.city-study { top: -36px; left: -45px; width: min(660px, 65vw); color: var(--sketch-city); stroke: currentColor; opacity: .34; }
.ecosystem-study { bottom: -32px; right: -35px; width: min(510px, 50vw); color: var(--sketch-leaf); stroke: currentColor; opacity: .4; }
.survey-lines, .contours { opacity: .35; stroke-width: .7; }
.pencil-detail { opacity: .6; stroke-width: .65; }
.sketch-wash { fill: currentColor; fill-opacity: .08; }
@media (max-width: 768px) { .city-study { width: 85vw; opacity: .22; } .ecosystem-study { width: 75vw; opacity: .25; } }
</style>
