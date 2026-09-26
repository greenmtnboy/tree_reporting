import { readFile, writeFile } from 'node:fs/promises'
import process from 'node:process'
import { generateCitySvg } from './city.mjs'

const destination = new URL('../field-sketches/city.svg', import.meta.url)
const svg = generateCitySvg()
if (process.argv.includes('--check')) {
  const existing = (await readFile(destination, 'utf8')).replaceAll('\r\n', '\n')
  if (existing !== svg) throw new Error('city.svg is stale. Run pnpm artwork:generate.')
  console.log('city.svg matches its generator.')
} else {
  await writeFile(destination, svg)
  console.log('Generated city.svg from shared ground-plane geometry.')
}
