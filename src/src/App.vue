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
import { useRoute, useRouter } from 'vue-router'
import AppSidebar from './components/AppSidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import MobileLayout from './components/MobileLayout.vue'
import TopBar from './components/TopBar.vue'
import WelcomeModal from './components/WelcomeModal.vue'
import { getCityBiome } from './composables/dashboardContextSource'
import { useIsMobile } from './composables/useIsMobile'
import { CITY_CONFIG, useMapData, type CityCode } from './composables/useMapData'
import { useMapLifecycle } from './composables/useMapLifecycle'

const { isMobile } = useIsMobile()
const { selectedCity } = useMapData()
const { activateCity } = useMapLifecycle()
const route = useRoute()
const router = useRouter()

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

const BIOME_BACKGROUND_SIZE = '100vw auto'
const BIOME_ACCENT_SIZE = '100vw auto'
const BIOME_TREE_SIZE = '100vw auto'
const BIOME_BACKGROUND_POSITION = 'center bottom'
const BIOME_ACCENT_POSITION = 'center bottom'
const BIOME_TREE_POSITION = 'center bottom'

function readRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  if (typeof city !== 'string') return null
  return city in CITY_CONFIG ? (city as CityCode) : null
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

function makeConiferBackgroundSvg(season: SeasonName) {
  const palette = {
    spring: { far: '#172e24', near: '#1e3d2c', floor: '#2c5a3e' },
    summer: { far: '#102418', near: '#163222', floor: '#224836' },
    autumn: { far: '#1a2e22', near: '#253c2c', floor: '#2e4e38' },
    winter: { far: '#18262e', near: '#223240', floor: '#2e404e' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M0 900V480C80 452 150 424 220 394C290 362 360 340 440 316C510 296 580 300 650 278C720 256 800 240 900 222C940 214 960 228 980 218V900Z'
        fill='${palette.far}' fill-opacity='.2'/>
      <path d='M0 900V680C28 654 52 622 80 602C100 588 118 604 140 580C162 556 172 530 200 518C220 508 242 526 264 506C286 486 298 462 328 452C350 444 372 464 396 446C418 430 432 408 460 400C482 392 506 412 530 396C552 380 566 358 596 350C618 342 642 362 670 344C692 328 710 308 746 300C768 292 794 312 826 292C852 278 876 258 930 246C956 238 970 254 980 244V900Z'
        fill='${palette.near}' fill-opacity='.2'/>
      <path d='M0 900V808C70 792 140 782 210 770C290 756 370 748 460 738C540 730 620 736 700 724C778 714 858 700 980 686V900Z'
        fill='${palette.floor}' fill-opacity='.15'/>
    </svg>
  `)
}

function makeConiferAccentSvg(season: SeasonName) {
  const palette = {
    spring: { mist: '#9ec8bc', stream: '#68b0c4' },
    summer: { mist: '#80b4aa', stream: '#50a0b8' },
    autumn: { mist: '#8caaa4', stream: '#5c8898' },
    winter: { mist: '#a8c4cc', stream: '#6e9cb0' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M0 700C120 680 240 668 360 658C480 648 580 654 700 642C800 632 890 626 980 616V720C880 734 780 742 680 750C560 760 440 756 340 766C220 778 110 794 0 814Z'
        fill='${palette.mist}' fill-opacity='.08'/>
      <path d='M400 900C434 852 462 818 498 782C534 746 566 734 608 708C648 684 692 684 736 656C778 628 820 578 888 536C928 512 956 518 980 506V580C952 596 924 616 882 652C828 694 782 744 736 778C692 810 646 822 602 848C558 874 520 900 476 900Z'
        fill='${palette.stream}' fill-opacity='.13'/>
    </svg>
  `)
}

function makeConiferTreeSvg(season: SeasonName) {
  const palette = {
    spring: { trunk: '#3a2c1c', canopy: '#2e6e48', snow: 'none' },
    summer: { trunk: '#3a2c1c', canopy: '#1e5c38', snow: 'none' },
    autumn: { trunk: '#3e2e1e', canopy: '#2a5c40', snow: 'none' },
    winter: { trunk: '#4a5860', canopy: '#2a4c4a', snow: '#ccdde4' },
  }[season]

  const snowAccents = season === 'winter'
    ? `
      <g fill='${palette.snow}' fill-opacity='.18'>
        <polygon points='560,510 516,558 604,558'/>
        <polygon points='560,466 522,508 598,508'/>
        <polygon points='750,488 710,534 790,534'/>
        <polygon points='750,444 714,484 786,484'/>
      </g>`
    : ''

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <g fill='${palette.trunk}' fill-opacity='.22'>
        <rect x='554' y='598' width='12' height='222' rx='4'/>
        <rect x='745' y='576' width='10' height='244' rx='4'/>
      </g>
      <g fill='${palette.canopy}'>
        <polygon points='560,608 490,684 630,684' fill-opacity='.22'/>
        <polygon points='560,558 498,628 622,628' fill-opacity='.22'/>
        <polygon points='560,512 506,574 614,574' fill-opacity='.2'/>
        <polygon points='560,470 512,526 608,526' fill-opacity='.19'/>
        <polygon points='560,432 518,480 602,480' fill-opacity='.17'/>
        <polygon points='560,400 522,442 598,442' fill-opacity='.15'/>
        <polygon points='560,372 530,406 590,406' fill-opacity='.13'/>
        <polygon points='750,588 684,660 816,660' fill-opacity='.2'/>
        <polygon points='750,540 690,606 810,606' fill-opacity='.2'/>
        <polygon points='750,496 696,554 804,554' fill-opacity='.18'/>
        <polygon points='750,456 702,508 798,508' fill-opacity='.17'/>
        <polygon points='750,420 708,464 792,464' fill-opacity='.16'/>
        <polygon points='750,388 712,426 788,426' fill-opacity='.14'/>
        <polygon points='750,360 718,392 782,392' fill-opacity='.13'/>
        <polygon points='750,336 722,362 778,362' fill-opacity='.11'/>
      </g>
      ${snowAccents}
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

function makeGrasslandBackgroundSvg(season: SeasonName) {
  const palette = {
    spring: { sky: '#6d9f8c', far: '#6fa15e', near: '#b7c66b' },
    summer: { sky: '#63866d', far: '#7f8e43', near: '#c2a457' },
    autumn: { sky: '#7c6548', far: '#9a753d', near: '#d09b57' },
    winter: { sky: '#6b7777', far: '#8a8d74', near: '#b9b39a' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M0 900V510C110 486 210 472 316 462C430 450 536 456 650 434C766 412 860 372 980 330V900Z'
        fill='${palette.sky}' fill-opacity='.13'/>
      <path d='M0 900V690C142 650 268 626 402 612C536 598 646 612 760 588C852 568 918 538 980 510V900Z'
        fill='${palette.far}' fill-opacity='.18'/>
      <path d='M0 900V798C150 768 282 754 410 744C540 734 660 746 782 728C858 718 924 698 980 674V900Z'
        fill='${palette.near}' fill-opacity='.16'/>
    </svg>
  `)
}

function makeGrasslandAccentSvg(season: SeasonName) {
  const palette = {
    spring: { river: '#86c7bc', seed: '#e2d99a' },
    summer: { river: '#6eaa9a', seed: '#e0bd70' },
    autumn: { river: '#7f9a8f', seed: '#e2a35f' },
    winter: { river: '#9fb1ad', seed: '#d6ccb0' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M340 900C410 840 488 800 570 770C646 742 720 724 790 690C860 656 914 612 980 552V642C914 700 848 754 780 792C708 832 630 846 548 874C506 888 466 900 420 900Z'
        fill='${palette.river}' fill-opacity='.12'/>
      <g fill='${palette.seed}' fill-opacity='.16'>
        <circle cx='612' cy='650' r='8'/>
        <circle cx='706' cy='612' r='7'/>
        <circle cx='810' cy='570' r='7'/>
        <circle cx='894' cy='528' r='6'/>
      </g>
    </svg>
  `)
}

function makeGrasslandTreeSvg(season: SeasonName) {
  const palette = {
    spring: { stem: '#5e4b2f', grass: '#9fc46d', canopy: '#74a85d' },
    summer: { stem: '#5b482d', grass: '#c1a458', canopy: '#8c8f44' },
    autumn: { stem: '#63432b', grass: '#d08a45', canopy: '#aa763f' },
    winter: { stem: '#65645b', grass: '#aca385', canopy: '#8d8d78' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <g stroke='${palette.grass}' stroke-opacity='.24' stroke-width='5' stroke-linecap='round' fill='none'>
        <path d='M544 820C550 742 572 690 612 628'/>
        <path d='M590 830C596 760 620 706 672 638'/>
        <path d='M674 828C682 750 714 684 776 604'/>
        <path d='M742 830C748 760 782 700 850 626'/>
        <path d='M812 828C818 768 846 718 900 658'/>
      </g>
      <g fill='${palette.stem}' fill-opacity='.2'>
        <rect x='636' y='568' width='12' height='236' rx='6'/>
        <rect x='806' y='600' width='10' height='204' rx='5'/>
      </g>
      <g fill='${palette.canopy}' fill-opacity='.18'>
        <ellipse cx='642' cy='548' rx='92' ry='42'/>
        <ellipse cx='808' cy='584' rx='70' ry='34'/>
      </g>
    </svg>
  `)
}

function makeDesertBackgroundSvg(season: SeasonName) {
  const palette = {
    spring: { far: '#6f6b45', dune: '#b99a62', wash: '#d3b178' },
    summer: { far: '#6f5b36', dune: '#b8874f', wash: '#d4a15c' },
    autumn: { far: '#704c35', dune: '#b47749', wash: '#cf8f5f' },
    winter: { far: '#656c68', dune: '#a99676', wash: '#c0b196' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M0 900V560C104 512 210 484 318 462C436 438 548 450 664 426C776 402 874 354 980 286V900Z'
        fill='${palette.far}' fill-opacity='.16'/>
      <path d='M0 900V720C120 662 248 626 382 614C520 602 640 638 768 612C850 596 920 562 980 520V900Z'
        fill='${palette.dune}' fill-opacity='.17'/>
      <path d='M0 900V814C136 776 276 760 420 764C558 768 700 800 830 778C890 768 940 748 980 724V900Z'
        fill='${palette.wash}' fill-opacity='.13'/>
    </svg>
  `)
}

function makeDesertAccentSvg(season: SeasonName) {
  const palette = {
    spring: { bloom: '#f0c88a', shadow: '#7aa07b' },
    summer: { bloom: '#e1aa68', shadow: '#8a8f65' },
    autumn: { bloom: '#e1945d', shadow: '#92705f' },
    winter: { bloom: '#d8c7a7', shadow: '#8aa0a0' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <path d='M518 900C600 834 666 804 742 774C818 744 886 700 980 620V732C912 784 842 826 764 852C690 878 622 896 552 900Z'
        fill='${palette.shadow}' fill-opacity='.11'/>
      <g fill='${palette.bloom}' fill-opacity='.17'>
        <circle cx='690' cy='646' r='8'/>
        <circle cx='760' cy='612' r='7'/>
        <circle cx='834' cy='666' r='7'/>
      </g>
    </svg>
  `)
}

function makeDesertTreeSvg(season: SeasonName) {
  const palette = {
    spring: { cactus: '#6d9b72', spine: '#d7caa2', trunk: '#68482f' },
    summer: { cactus: '#778958', spine: '#d3b577', trunk: '#67442b' },
    autumn: { cactus: '#88744f', spine: '#d79b6a', trunk: '#70452f' },
    winter: { cactus: '#7f9186', spine: '#d8d4c5', trunk: '#69706c' },
  }[season]

  return svgUrl(`
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 980 900'>
      <g fill='${palette.cactus}' fill-opacity='.2'>
        <rect x='682' y='482' width='36' height='318' rx='18'/>
        <rect x='616' y='568' width='28' height='132' rx='14'/>
        <rect x='754' y='560' width='28' height='144' rx='14'/>
        <rect x='810' y='618' width='24' height='156' rx='12'/>
      </g>
      <g fill='${palette.trunk}' fill-opacity='.18'>
        <rect x='630' y='688' width='14' height='86' rx='7'/>
        <rect x='754' y='694' width='14' height='82' rx='7'/>
      </g>
      <g stroke='${palette.spine}' stroke-opacity='.12' stroke-width='3' stroke-linecap='round'>
        <path d='M692 530H710'/>
        <path d='M690 620H712'/>
        <path d='M690 716H712'/>
      </g>
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
      backgroundSize: BIOME_BACKGROUND_SIZE,
      accentSize: BIOME_ACCENT_SIZE,
      treeSize: BIOME_TREE_SIZE,
      backgroundPosition: BIOME_BACKGROUND_POSITION,
      accentPosition: BIOME_ACCENT_POSITION,
      treePosition: BIOME_TREE_POSITION,
      opacity: season === 'summer' ? '0.94' : '0.88',
    }
  }

  if (normalizedBiome.includes('conifer')) {
    const seasonal = {
      spring: ['rgba(46, 120, 90, 0.26)', 'rgba(110, 190, 158, 0.13)', 'rgba(14, 44, 32, 0.2)'],
      summer: ['rgba(24, 96, 66, 0.34)', 'rgba(72, 154, 118, 0.16)', 'rgba(10, 36, 26, 0.2)'],
      autumn: ['rgba(52, 106, 82, 0.28)', 'rgba(96, 154, 124, 0.13)', 'rgba(20, 38, 30, 0.2)'],
      winter: ['rgba(46, 88, 110, 0.26)', 'rgba(130, 172, 192, 0.12)', 'rgba(16, 30, 40, 0.22)'],
    }[season]

    return {
      glowPrimary: seasonal[0],
      glowSecondary: seasonal[1],
      wash: seasonal[2],
      backgroundArt: makeConiferBackgroundSvg(season),
      accentArt: makeConiferAccentSvg(season),
      treeArt: makeConiferTreeSvg(season),
      backgroundSize: BIOME_BACKGROUND_SIZE,
      accentSize: BIOME_ACCENT_SIZE,
      treeSize: BIOME_TREE_SIZE,
      backgroundPosition: BIOME_BACKGROUND_POSITION,
      accentPosition: BIOME_ACCENT_POSITION,
      treePosition: BIOME_TREE_POSITION,
      opacity: season === 'winter' ? '0.9' : '0.86',
    }
  }

  if (
    !normalizedBiome.includes('desert')
    && !normalizedBiome.includes('xeric')
    && (
      normalizedBiome.includes('grassland')
      || normalizedBiome.includes('savanna')
      || normalizedBiome.includes('shrubland')
    )
  ) {
    const seasonal = {
      spring: ['rgba(169, 196, 96, 0.28)', 'rgba(132, 204, 184, 0.12)', 'rgba(48, 50, 28, 0.18)'],
      summer: ['rgba(199, 160, 72, 0.3)', 'rgba(118, 160, 112, 0.13)', 'rgba(54, 44, 24, 0.2)'],
      autumn: ['rgba(210, 136, 70, 0.28)', 'rgba(228, 178, 98, 0.12)', 'rgba(56, 38, 24, 0.2)'],
      winter: ['rgba(166, 160, 124, 0.24)', 'rgba(168, 188, 184, 0.1)', 'rgba(44, 44, 36, 0.2)'],
    }[season]

    return {
      glowPrimary: seasonal[0],
      glowSecondary: seasonal[1],
      wash: seasonal[2],
      backgroundArt: makeGrasslandBackgroundSvg(season),
      accentArt: makeGrasslandAccentSvg(season),
      treeArt: makeGrasslandTreeSvg(season),
      backgroundSize: BIOME_BACKGROUND_SIZE,
      accentSize: BIOME_ACCENT_SIZE,
      treeSize: BIOME_TREE_SIZE,
      backgroundPosition: BIOME_BACKGROUND_POSITION,
      accentPosition: BIOME_ACCENT_POSITION,
      treePosition: BIOME_TREE_POSITION,
      opacity: season === 'summer' ? '0.92' : '0.88',
    }
  }

  if (normalizedBiome.includes('desert') || normalizedBiome.includes('xeric')) {
    const seasonal = {
      spring: ['rgba(207, 167, 86, 0.28)', 'rgba(116, 156, 112, 0.12)', 'rgba(58, 44, 28, 0.2)'],
      summer: ['rgba(218, 150, 74, 0.32)', 'rgba(154, 128, 74, 0.14)', 'rgba(60, 38, 24, 0.23)'],
      autumn: ['rgba(208, 122, 76, 0.3)', 'rgba(218, 160, 104, 0.12)', 'rgba(58, 36, 26, 0.22)'],
      winter: ['rgba(180, 164, 126, 0.24)', 'rgba(138, 166, 164, 0.1)', 'rgba(44, 44, 38, 0.2)'],
    }[season]

    return {
      glowPrimary: seasonal[0],
      glowSecondary: seasonal[1],
      wash: seasonal[2],
      backgroundArt: makeDesertBackgroundSvg(season),
      accentArt: makeDesertAccentSvg(season),
      treeArt: makeDesertTreeSvg(season),
      backgroundSize: BIOME_BACKGROUND_SIZE,
      accentSize: BIOME_ACCENT_SIZE,
      treeSize: BIOME_TREE_SIZE,
      backgroundPosition: BIOME_BACKGROUND_POSITION,
      accentPosition: BIOME_ACCENT_POSITION,
      treePosition: BIOME_TREE_POSITION,
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
      backgroundSize: BIOME_BACKGROUND_SIZE,
      accentSize: BIOME_ACCENT_SIZE,
      treeSize: BIOME_TREE_SIZE,
      backgroundPosition: BIOME_BACKGROUND_POSITION,
      accentPosition: BIOME_ACCENT_POSITION,
      treePosition: BIOME_TREE_POSITION,
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
    backgroundSize: BIOME_BACKGROUND_SIZE,
    accentSize: BIOME_ACCENT_SIZE,
    treeSize: BIOME_TREE_SIZE,
    backgroundPosition: BIOME_BACKGROUND_POSITION,
    accentPosition: BIOME_ACCENT_POSITION,
    treePosition: BIOME_TREE_POSITION,
    opacity: '0.86',
  }
}

const activeBackgroundTheme = computed(() => {
  const biome = getCityBiome(selectedCity.value)
  return buildBackgroundTheme(biome, getSeason())
})

void router.isReady().then(() => {
  const city = readRouteCity(route.query.city)
  if (city && city !== selectedCity.value) {
    activateCity(city)
  }
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
