import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { test } from 'node:test'

// The review pages build their cards from template strings. Values the page
// escapes go through esc(); coordinates go into an href through osm(), which
// has to hold on its own, because the documents behind them are client-written.
// This runs each page's own osm() and esc() rather than a copy of them.
function pageHelpers(file: string): { osm: (lat: unknown, lng: unknown) => string; esc: (v: unknown) => string } {
  const html = readFileSync(path.join(import.meta.dirname, '..', file), 'utf8')
  const esc = html.match(/^const esc = .*;$/m)?.[0]
  const osm = html.match(/^const osm = [\s\S]*?^};$/m)?.[0]
  assert.ok(esc && osm, `${file} defines esc() and osm() at the top level`)
  return new Function(`${esc}\n${osm}\nreturn { osm, esc };`)()
}

const HOSTILE = [
  '1"><img src=x onerror=alert(1)>',
  "1' onmouseover='alert(1)",
  'javascript:alert(1)',
  { toString: () => '"><script>alert(1)</script>' },
]

for (const file of ['checkin_photos_page.html', 'modifications_page.html']) {
  test(`${file}: a hostile coordinate never reaches the markup`, () => {
    const { osm } = pageHelpers(file)
    for (const value of HOSTILE) {
      assert.equal(osm(value, 2), '\u2014')
      assert.equal(osm(1, value), '\u2014')
    }
    assert.equal(osm(null, null), '\u2014')
    assert.equal(osm(Number.NaN, 1), '\u2014')
  })

  test(`${file}: a real coordinate links to OpenStreetMap`, () => {
    const { osm } = pageHelpers(file)
    assert.equal(
      osm(37.7749, -122.4194),
      '<a href="https://www.openstreetmap.org/?mlat=37.7749&mlon=-122.4194#map=20/37.7749/-122.4194" target="_blank" rel="noopener">37.774900, -122.419400</a>',
    )
  })

  test(`${file}: esc() neutralises markup`, () => {
    const { esc } = pageHelpers(file)
    assert.equal(esc('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;')
  })
}
