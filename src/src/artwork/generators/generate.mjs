import { readFile, writeFile } from 'node:fs/promises'
import process from 'node:process'
import { generateCitySvg } from './city.mjs'
import { generateGrasslandSvg } from './grassland.mjs'
import { generateBroadleafSvg } from './broadleaf.mjs'
import { generateWoodlandSvg } from './woodland.mjs'

const generators = [
  ['city', generateCitySvg],
  ['grassland', generateGrasslandSvg],
  ['broadleaf', generateBroadleafSvg],
  ['woodland', generateWoodlandSvg],
]

for (const [name, generate] of generators) {
  const destination = new URL(`../field-sketches/${name}.svg`, import.meta.url)
  const svg = generate()
  if (process.argv.includes('--check')) {
    const existing = (await readFile(destination, 'utf8')).replaceAll('\r\n', '\n')
    if (existing !== svg) throw new Error(`${name}.svg is stale. Run pnpm artwork:generate.`)
    console.log(`${name}.svg matches its generator.`)
  } else {
    await writeFile(destination, svg)
    console.log(`Generated ${name}.svg from its scene geometry.`)
  }
}
