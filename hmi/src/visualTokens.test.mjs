import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'

const exported = JSON.parse(readFileSync(new URL('../design/visual-v2.tokens.json', import.meta.url)))
const css = readFileSync(new URL('./aurora.css', import.meta.url), 'utf8')
const declarations = body => Object.fromEntries([...body.matchAll(/(--au-[\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2]]))
function snapshotMatches(sheet) {
  for (const collection of exported.collections) for (const [index, mode] of collection.modes.entries()) {
    const marker = `/* ${collection.name} / ${mode} */`
    const at = sheet.indexOf(marker)
    assert.ok(at >= 0, marker)
    const block = declarations(sheet.slice(sheet.indexOf('{', at) + 1, sheet.indexOf('}', at)))
    for (const [name, syntax, ...values] of collection.variables) {
      const value = values[index]
      const unit = typeof value === 'number' ? collection.name === 'Motion' ? 'ms' : 'px' : ''
      assert.equal(block[syntax.slice(4, -1)], `${value}${unit}`, `${collection.name}/${mode}/${name}`)
    }
    assert.equal(Object.keys(block).length, collection.variables.length)
  }
}
test('all five collections and four Size modes match the frozen Figma MCP export', () => snapshotMatches(css))
test('the token comparison rejects a missing or altered declaration', () => {
  assert.throws(() => snapshotMatches(css.replace('--au-type-body-size: 24px;', '')))
  assert.throws(() => snapshotMatches(css.replace('--au-text-2: #ffffffb2;', '--au-text-2: #ffffff20;')))
})

function rgba(hex) {
  assert.match(hex, /^#[0-9a-f]{6}([0-9a-f]{2})?$/i)
  return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).concat(hex.length === 9 ? parseInt(hex.slice(7), 16) / 255 : 1)
}
const over = (fg, bg) => fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3])).concat(1)
const luminance = c => c.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0)
const contrast = (fg, bg) => {
  const a = luminance(over(fg, bg)), b = luminance(bg)
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05)
}
for (const theme of ['Dark', 'Light']) test(`${theme}: text/icon contrast survives the brightest content under the scrim and each surface`, () => {
  const marker = `/* Color / ${theme} */`
  const at = css.indexOf(marker)
  const v = Object.fromEntries(Object.entries(declarations(css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at)))).map(([k, val]) => [k, rgba(val)]))
  for (const stage of [v['--au-stage'], rgba('#ffffff'), v['--au-map-route']]) {
    const panel = over(v['--au-material-panel'], over(v['--au-stage-scrim'], stage))
    const surfaces = [v['--au-bg'], panel, ...[1, 2, 3].map(i => over(v[`--au-surface-${i}`], panel)), v['--au-material-solid']]
    for (const bg of surfaces) for (const [key, threshold] of [['text', 4.5], ['text-2', 4.5], ['text-3', 3], ['icon', 3]]) {
      assert.ok(contrast(v[`--au-${key}`], bg) >= threshold, `${key} ${contrast(v[`--au-${key}`], bg).toFixed(3)} < ${threshold}`)
    }
    // Confirmation/errors use the opaque M0 surface; colored data never replaces body text.
    for (const key of ['primary', 'warn', 'danger']) {
      const bg = over(v[`--au-${key}-soft`], v['--au-material-solid'])
      assert.ok(contrast(v[`--au-${key}`], bg) >= 4.5, `${key} on soft: ${contrast(v[`--au-${key}`], bg)}`)
    }
  }
})

test('self hosted variable fonts match their recorded bytes; the entry page has no remote font requests', () => {
  const metadata = JSON.parse(readFileSync(new URL('../public/fonts/visual-v2-fonts.json', import.meta.url)))
  for (const font of metadata.fonts) {
    const bytes = readFileSync(new URL(`../public/fonts/${font.file}`, import.meta.url))
    assert.equal(bytes.subarray(0, 4).toString(), 'wOF2')
    assert.equal(createHash('sha256').update(bytes).digest('hex'), font.sha256)
  }
  assert.doesNotMatch(readFileSync(new URL('../index.html', import.meta.url), 'utf8'), /fonts\.(googleapis|gstatic)\.com/)
})
