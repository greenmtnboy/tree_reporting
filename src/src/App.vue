<template>
  <MobileLayout v-if="isMobile" />
  <template v-else>
    <AppSidebar />
    <div class="main-col">
      <TopBar />
      <main class="main-content">
        <router-view />
      </main>
    </div>
    <ChatPanel />
  </template>
  <WelcomeModal />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, watchEffect } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import MobileLayout from './components/MobileLayout.vue'
import TopBar from './components/TopBar.vue'
import WelcomeModal from './components/WelcomeModal.vue'
import { getCityBiome } from './composables/dashboardContextSource'
import { useIsMobile } from './composables/useIsMobile'
import { useMapData } from './composables/useMapData'

const { isMobile } = useIsMobile()
const { selectedCity } = useMapData()

type SeasonName = 'spring' | 'summer' | 'autumn' | 'winter'
type BackgroundTheme = {
  glowPrimary: string
  glowSecondary: string
  wash: string
  backgroundArt: string
  accentArt: string
  treeArt: string
  backgroundSize: string
  accentSize: string
  treeSize: string
  backgroundPosition: string
  accentPosition: string
  treePosition: string
  opacity: string
}

function svgUrl(svg: string) {
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
}

function getSeason(date = new Date()): SeasonName {
  const month = date.getMonth() + 1
  if (month >= 3 && month <= 5) return 'spring'
  if (month >= 6 && month <= 8) return 'summer'
  if (month >= 9 && month <= 11) return 'autumn'
  return 'winter'
}

function makeTemperateBackgroundSvg(season: SeasonName) {
  const palette = {
    spring: { far: '#223a2c', near: '#3f8259', ridge: '#8fbf9f' },
    summer: { far: '#1a3426', near: '#2f7d4f', ridge: '#6baf92' },
    autumn: { far: '#3d2b1d', near: '#7e5b3a', ridge: '#c18a58' },
    winter: { far: '#24323a', near: '#506b74', ridge: '#8fa7ad' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M0 900V640C84 588 138 544 206 514C274 484 348 450 436 422C522 394 604 366 680 344C760 320 840 298 980 254V900Z'
        fill='${palette.far}' fill-opacity='.2'/>
      <path d='M0 900V744C122 704 220 676 320 648C432 618 532 596 636 576C736 556 834 528 980 484V900Z'
        fill='${palette.near}' fill-opacity='.18'/>
      <path d='M0 900V804C126 784 246 772 366 756C492 738 612 724 722 708C840 692 910 672 980 644V900Z'
        fill='${palette.ridge}' fill-opacity='.12'/>
    </svg>
  `)
}

function makeTemperateAccentSvg(season: SeasonName) {
  const palette = {
    spring: { river: '#8fd0d8', meadow: '#b6dba5' },
    summer: { river: '#6fb4c8', meadow: '#89b37c' },
    autumn: { river: '#7398af', meadow: '#c49a61' },
    winter: { river: '#90aebb', meadow: '#9eafb0' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M210 900C272 814 324 758 378 736C448 708 498 734 568 706C638 678 662 614 748 606C818 598 884 640 980 692V900Z'
        fill='${palette.meadow}' fill-opacity='.1'/>
      <path d='M0 900C120 846 196 818 274 768C346 722 404 638 482 618C560 598 646 650 724 634C808 616 872 560 980 496V600C880 666 804 722 742 764C684 804 630 842 560 876C498 906 420 900 346 900Z'
        fill='${palette.river}' fill-opacity='.12'/>
    </svg>
  `)
}

function makeTemperateTreeSvg(season: SeasonName) {
  const palette = {
    spring: { trunk: '#483829', canopy: '#86bf7f', accent: '#e4c1dd' },
    summer: { trunk: '#423324', canopy: '#2f7d4f', accent: '#a7e3b2' },
    autumn: { trunk: '#49311f', canopy: '#b86f43', accent: '#d8b567' },
    winter: { trunk: '#55646b', canopy: '#55646b', accent: '#d6dce0' },
  }[season]

  const crown = season === 'winter'
    ? `
      <g stroke='${palette.canopy}' stroke-opacity='.22' stroke-width='5' stroke-linecap='round' fill='none'>
        <path d='M560 582C546 528 520 494 474 454'/>
        <path d='M560 578C608 526 642 490 674 430'/>
        <path d='M742 614C716 550 696 516 654 470'/>
        <path d='M742 610C784 560 818 522 852 464'/>
      </g>`
    : `
      <g>
        <circle cx='494' cy='430' r='74' fill='${palette.canopy}' fill-opacity='.2'/>
        <circle cx='586' cy='382' r='88' fill='${palette.canopy}' fill-opacity='.22'/>
        <circle cx='704' cy='446' r='80' fill='${palette.canopy}' fill-opacity='.22'/>
        <circle cx='798' cy='398' r='70' fill='${palette.canopy}' fill-opacity='.18'/>
        <g fill='${palette.accent}' fill-opacity='${season === 'spring' ? '.16' : '.1'}'>
          <circle cx='528' cy='360' r='12'/>
          <circle cx='642' cy='328' r='11'/>
          <circle cx='752' cy='366' r='11'/>
        </g>
      </g>`

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <g fill='${palette.trunk}' fill-opacity='.22'>
        <rect x='548' y='520' width='18' height='260' rx='8'/>
        <rect x='734' y='552' width='16' height='228' rx='8'/>
      </g>
      ${crown}
    </svg>
  `)
}

function makeMediterraneanBackgroundSvg(season: SeasonName) {
  const palette = {
    spring: { far: '#2d3827', near: '#658d58', ridge: '#b8d48f' },
    summer: { far: '#293422', near: '#778e52', ridge: '#b4b16a' },
    autumn: { far: '#3a3020', near: '#8f7446', ridge: '#c8a35c' },
    winter: { far: '#283138', near: '#63737b', ridge: '#98a5a1' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M266 900L466 420L626 704L760 318L980 900Z' fill='${palette.far}' fill-opacity='.2'/>
      <path d='M414 900L554 566L664 716L780 478L940 900Z' fill='${palette.near}' fill-opacity='.18'/>
      <path d='M598 900L682 690L760 790L836 626L938 900Z' fill='${palette.ridge}' fill-opacity='.12'/>
    </svg>
  `)
}

function makeMediterraneanAccentSvg(season: SeasonName) {
  const palette = {
    spring: { coast: '#7fc9cf', field: '#d9d28a' },
    summer: { coast: '#63adc0', field: '#c9b66f' },
    autumn: { coast: '#6e98aa', field: '#d2a55f' },
    winter: { coast: '#8faab2', field: '#b6beb8' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M540 900C618 852 678 820 740 766C802 714 866 640 980 520V640C914 724 862 786 804 838C746 888 678 900 600 900Z'
        fill='${palette.coast}' fill-opacity='.12'/>
      <path d='M316 900C404 836 502 814 602 804C690 794 782 790 896 760V900Z'
        fill='${palette.field}' fill-opacity='.1'/>
    </svg>
  `)
}

function makeMediterraneanTreeSvg(season: SeasonName) {
  const palette = {
    spring: { trunk: '#4c3a28', canopy: '#6d9654', accent: '#efe1a3' },
    summer: { trunk: '#4d3c27', canopy: '#6d8851', accent: '#d8c97a' },
    autumn: { trunk: '#543d29', canopy: '#9f7d49', accent: '#deb06e' },
    winter: { trunk: '#5a676c', canopy: '#7b8d83', accent: '#d3d8cf' },
  }[season]

  const seasonalAccent = season === 'winter'
    ? ``
    : `<g fill='${palette.accent}' fill-opacity='.12'>
        <circle cx='584' cy='458' r='10'/>
        <circle cx='776' cy='520' r='11'/>
      </g>`

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <g fill='${palette.trunk}' fill-opacity='.22'>
        <rect x='564' y='560' width='14' height='220' rx='7'/>
        <rect x='768' y='584' width='14' height='196' rx='7'/>
      </g>
      <g fill='${palette.canopy}' fill-opacity='.2'>
        <ellipse cx='572' cy='482' rx='118' ry='56'/>
        <ellipse cx='774' cy='532' rx='30' ry='168'/>
      </g>
      ${seasonalAccent}
    </svg>
  `)
}

function makeFallbackBackgroundSvg(season: SeasonName) {
  const palette = {
    spring: { far: '#223a2c', near: '#3f8259' },
    summer: { far: '#1f3528', near: '#2f7d4f' },
    autumn: { far: '#3d2b1d', near: '#a9683f' },
    winter: { far: '#28343a', near: '#4f6872' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M0 900V688C144 622 270 588 384 552C492 520 630 474 744 438C844 408 920 378 980 350V900Z'
        fill='${palette.far}' fill-opacity='.2'/>
      <path d='M0 900V792C148 768 264 742 384 712C500 682 616 656 742 622C850 594 922 564 980 532V900Z'
        fill='${palette.near}' fill-opacity='.14'/>
    </svg>
  `)
}

function makeFallbackAccentSvg(season: SeasonName) {
  const palette = {
    spring: '#a7e3b2',
    summer: '#6baf92',
    autumn: '#d9a166',
    winter: '#aebbc0',
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M340 900C458 826 570 810 662 792C754 774 842 738 980 664V900Z'
        fill='${palette}' fill-opacity='.1'/>
    </svg>
  `)
}

function makeFallbackTreeSvg(season: SeasonName) {
  const palette = {
    spring: { trunk: '#433425', canopy: '#7ab67d' },
    summer: { trunk: '#433425', canopy: '#2f7d4f' },
    autumn: { trunk: '#473121', canopy: '#b86f43' },
    winter: { trunk: '#59676d', canopy: '#6c7c82' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <g fill='${palette.trunk}' fill-opacity='.22'>
        <rect x='628' y='566' width='14' height='214' rx='7'/>
      </g>
      <g fill='${palette.canopy}' fill-opacity='.2'>
        <polygon points='634,378 548,540 720,540'/>
        <polygon points='634,442 566,592 702,592'/>
      </g>
    </svg>
  `)
}

function buildBackgroundTheme(biome: string, season: SeasonName): BackgroundTheme {
  const normalizedBiome = biome.toLowerCase()

  if (normalizedBiome.includes('mediterranean')) {
    const seasonal = {
      spring: ['rgba(126, 182, 108, 0.28)', 'rgba(224, 205, 116, 0.14)', 'rgba(38, 62, 36, 0.18)'],
      summer: ['rgba(182, 176, 92, 0.26)', 'rgba(212, 162, 72, 0.12)', 'rgba(56, 68, 34, 0.2)'],
      autumn: ['rgba(190, 128, 66, 0.24)', 'rgba(223, 188, 119, 0.12)', 'rgba(60, 46, 28, 0.2)'],
      winter: ['rgba(121, 152, 156, 0.22)', 'rgba(196, 206, 200, 0.1)', 'rgba(34, 46, 50, 0.2)'],
    }[season]

    return {
      glowPrimary: seasonal[0],
      glowSecondary: seasonal[1],
      wash: seasonal[2],
      backgroundArt: makeMediterraneanBackgroundSvg(season),
      accentArt: makeMediterraneanAccentSvg(season),
      treeArt: makeMediterraneanTreeSvg(season),
      backgroundSize: 'min(76vw, 1120px) auto',
      accentSize: 'min(74vw, 1080px) auto',
      treeSize: 'min(62vw, 900px) auto',
      backgroundPosition: 'right -3vw bottom',
      accentPosition: 'right -1vw bottom -1vh',
      treePosition: 'right 4vw bottom -1vh',
      opacity: season === 'summer' ? '0.94' : '0.88',
    }
  }

  if (normalizedBiome.includes('temperate broadleaf') || normalizedBiome.includes('temperate')) {
    const seasonal = {
      spring: ['rgba(94, 179, 124, 0.24)', 'rgba(209, 177, 213, 0.12)', 'rgba(24, 48, 34, 0.18)'],
      summer: ['rgba(47, 125, 79, 0.34)', 'rgba(107, 175, 146, 0.16)', 'rgba(15, 36, 23, 0.18)'],
      autumn: ['rgba(184, 112, 58, 0.28)', 'rgba(217, 176, 97, 0.12)', 'rgba(46, 32, 20, 0.2)'],
      winter: ['rgba(96, 136, 152, 0.22)', 'rgba(184, 197, 205, 0.1)', 'rgba(22, 34, 42, 0.22)'],
    }[season]

    return {
      glowPrimary: seasonal[0],
      glowSecondary: seasonal[1],
      wash: seasonal[2],
      backgroundArt: makeTemperateBackgroundSvg(season),
      accentArt: makeTemperateAccentSvg(season),
      treeArt: makeTemperateTreeSvg(season),
      backgroundSize: 'min(78vw, 1160px) auto',
      accentSize: 'min(72vw, 1040px) auto',
      treeSize: 'min(62vw, 900px) auto',
      backgroundPosition: 'right -2vw bottom',
      accentPosition: 'right bottom',
      treePosition: 'right 3vw bottom',
      opacity: season === 'spring' ? '0.92' : '0.88',
    }
  }

  const seasonal = {
    spring: ['rgba(94, 179, 124, 0.22)', 'rgba(167, 227, 178, 0.12)', 'rgba(24, 44, 32, 0.18)'],
    summer: ['rgba(47, 125, 79, 0.28)', 'rgba(107, 175, 146, 0.14)', 'rgba(18, 36, 27, 0.2)'],
    autumn: ['rgba(184, 112, 58, 0.24)', 'rgba(217, 176, 97, 0.12)', 'rgba(40, 31, 23, 0.2)'],
    winter: ['rgba(96, 136, 152, 0.2)', 'rgba(184, 197, 205, 0.1)', 'rgba(24, 34, 40, 0.2)'],
  }[season]

  return {
    glowPrimary: seasonal[0],
    glowSecondary: seasonal[1],
    wash: seasonal[2],
    backgroundArt: makeFallbackBackgroundSvg(season),
    accentArt: makeFallbackAccentSvg(season),
    treeArt: makeFallbackTreeSvg(season),
    backgroundSize: 'min(74vw, 1080px) auto',
    accentSize: 'min(70vw, 1020px) auto',
    treeSize: 'min(56vw, 820px) auto',
    backgroundPosition: 'right bottom',
    accentPosition: 'right bottom',
    treePosition: 'right 5vw bottom',
    opacity: '0.86',
  }
}

const activeBackgroundTheme = computed(() => {
  const biome = getCityBiome(selectedCity.value)
  return buildBackgroundTheme(biome, getSeason())
})

watchEffect(() => {
  const appEl = document.getElementById('app')
  if (!appEl) return

  const theme = activeBackgroundTheme.value
  appEl.style.setProperty('--app-biome-glow-primary', theme.glowPrimary)
  appEl.style.setProperty('--app-biome-glow-secondary', theme.glowSecondary)
  appEl.style.setProperty('--app-biome-wash', theme.wash)
  appEl.style.setProperty('--app-biome-background-art', theme.backgroundArt)
  appEl.style.setProperty('--app-biome-accent-art', theme.accentArt)
  appEl.style.setProperty('--app-biome-tree-art', theme.treeArt)
  appEl.style.setProperty('--app-biome-background-size', theme.backgroundSize)
  appEl.style.setProperty('--app-biome-accent-size', theme.accentSize)
  appEl.style.setProperty('--app-biome-tree-size', theme.treeSize)
  appEl.style.setProperty('--app-biome-background-position', theme.backgroundPosition)
  appEl.style.setProperty('--app-biome-accent-position', theme.accentPosition)
  appEl.style.setProperty('--app-biome-tree-position', theme.treePosition)
  appEl.style.setProperty('--app-biome-opacity', theme.opacity)
})

onBeforeUnmount(() => {
  const appEl = document.getElementById('app')
  if (!appEl) return
  appEl.style.removeProperty('--app-biome-glow-primary')
  appEl.style.removeProperty('--app-biome-glow-secondary')
  appEl.style.removeProperty('--app-biome-wash')
  appEl.style.removeProperty('--app-biome-background-art')
  appEl.style.removeProperty('--app-biome-accent-art')
  appEl.style.removeProperty('--app-biome-tree-art')
  appEl.style.removeProperty('--app-biome-background-size')
  appEl.style.removeProperty('--app-biome-accent-size')
  appEl.style.removeProperty('--app-biome-tree-size')
  appEl.style.removeProperty('--app-biome-background-position')
  appEl.style.removeProperty('--app-biome-accent-position')
  appEl.style.removeProperty('--app-biome-tree-position')
  appEl.style.removeProperty('--app-biome-opacity')
})
</script>
