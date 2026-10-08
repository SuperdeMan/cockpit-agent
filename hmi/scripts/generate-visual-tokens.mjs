// I1: frozen Figma export -> the existing CSS declaration source.
// Run from any cwd: node hmi/scripts/generate-visual-tokens.mjs [--check]
import { readFileSync, writeFileSync } from 'node:fs'

const source = JSON.parse(readFileSync(new URL('../design/visual-v2.tokens.json', import.meta.url), 'utf8'))
const target = new URL('../src/aurora.css', import.meta.url)
const start = '/* BEGIN FIGMA VISUAL V2 TOKENS */'
const end = '/* END FIGMA VISUAL V2 TOKENS */'
const selectors = {
  Color: [':root', ":root[data-theme='light']"],
  Size: [':root', ":root[data-drive='on']", ":root[data-font='large']", ":root[data-drive='on'][data-font='large']"],
  Space: [':root'], Radius: [':root'], Motion: [':root'],
}
const blocks = source.collections.flatMap((collection) => collection.modes.map((mode, index) => {
  const values = collection.variables.map(([name, syntax, ...modes]) => {
    const key = /^var\((--au-[\w-]+)\)$/.exec(syntax)?.[1]
    if (!key) throw new Error(`Invalid CSS name: ${syntax}`)
    const value = modes[index]
    const unit = typeof value === 'number' ? collection.name === 'Motion' ? 'ms' : 'px' : ''
    return `  ${key}: ${value}${unit};`
  })
  return `/* ${collection.name} / ${mode} */\n${selectors[collection.name][index]} {\n${values.join('\n')}\n}`
}))
const generated = `${start}\n/* Figma ${source.fileKey}, ${source.exportedAt}. Regenerate; do not edit this block. */\n${blocks.join('\n\n')}\n${end}`
const current = readFileSync(target, 'utf8').replace(/\r\n/g, '\n')
const before = current.indexOf(start)
const after = current.indexOf(end)
if (before < 0 || after < before) throw new Error('Missing token block markers')
const next = current.slice(0, before) + generated + current.slice(after + end.length)
if (process.argv.includes('--check')) {
  if (next !== current) throw new Error('aurora.css differs from the frozen Figma export')
  console.log('Figma token snapshot matches aurora.css (5 collections, 4 size modes)')
} else writeFileSync(target, next)
