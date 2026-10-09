import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import snapshot from '../design/visual-v2.tokens.json'
import hmi from '../../hmi/design/visual-v2.tokens.json'

const css = readFileSync(resolve('src/tokens.css'), 'utf8')
type Color = [number, number, number, number]
const rgba = (hex: string): Color => {
  const value = hex.slice(1)
  return [0, 2, 4, 6].map((start, i) => i === 3 && value.length === 6 ? 1 : parseInt(value.slice(start, start + 2), 16) / 255) as Color
}
const over = (fg: Color, bg: Color): Color => [fg[0] * fg[3] + bg[0] * (1 - fg[3]), fg[1] * fg[3] + bg[1] * (1 - fg[3]), fg[2] * fg[3] + bg[2] * (1 - fg[3]), 1]
const luminance = (color: Color) => color.slice(0, 3).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0)
const contrast = (a: Color, b: Color) => { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05) }

describe('Figma H-01 visual token contract', () => {
  it('matches every exported CSS variable in each mode', () => {
    const selectors: Record<string, string[]> = { Color: [':root, [data-theme="dark"]', '[data-theme="light"]'], Size: [':root, [data-size="workbench"]', '[data-size="presentation"]'] }
    for (const collection of snapshot.collections.filter(c => c.name !== 'Primitives')) {
      collection.modes.forEach((_, mode) => {
        const selector = selectors[collection.name]?.[mode] || ':root '
        const start = css.indexOf(selector)
        const block = css.slice(start, css.indexOf('}', start))
        for (const variable of collection.variables) {
          const value = variable[mode + 2]
          const suffix = typeof value === 'number' ? collection.name === 'Motion' ? 'ms' : 'px' : ''
          expect(block, collection.name + '/' + variable[0]).toContain(`${String(variable[1]).slice(4, -1)}: ${value}${suffix};`)
        }
      })
    }
  })

  it('keeps shared brand primitives in agreement with HMI', () => {
    const ours = snapshot.collections.find(c => c.name === 'Color')!.variables
    const theirs = hmi.collections.find(c => c.name === 'Color')!.variables
    // HMI's frozen export exposes resolved Color roles rather than Primitives.
    const shared = { 'bg/page': 'bg', 'text/primary': 'text/primary', 'text/secondary': 'text/secondary', 'text/tertiary': 'text/tertiary', 'icon/default': 'icon/default', 'line/default': 'line/default', 'line/strong': 'line/strong', 'accent/default': 'accent/default', 'accent/on': 'accent/on', 'accent/soft': 'accent/soft', 'accent/line': 'accent/line', 'status/good/fg': 'success/default', 'status/warn/fg': 'warning/default', 'status/warn/soft': 'warning/soft', 'status/critical/fg': 'danger/default', 'status/critical/soft': 'danger/soft', 'shadow/color': 'shadow/color', 'scrim/modal': 'scrim/modal' }
    for (const [ourName, theirName] of Object.entries(shared)) {
      expect(ours.find(v => v[0] === ourName)!.slice(2), ourName).toEqual(theirs.find(v => v[0] === theirName)!.slice(2))
    }
    for (const name of ['aurora/cyan', 'aurora/blue', 'aurora/violet', 'aurora/magenta']) {
      expect(snapshot.collections.find(c => c.name === 'Primitives')!.variables.find(v => v[0] === name)![2]).toBe(theirs.find(v => v[0] === name)![2])
    }
  })

  it.each([0, 1])('meets F-01 contrast after compositing mode %s', mode => {
    const colors = Object.fromEntries(snapshot.collections.find(c => c.name === 'Color')!.variables.map(v => [v[0], rgba(String(v[mode + 2]))]))
    const backgrounds = [colors['bg/page'], colors['surface/1'], colors['surface/2'], over(colors['overlay/hover'], colors['surface/1']), over(colors['accent/soft'], colors['surface/1'])]
    for (const key of ['text/primary', 'text/secondary', 'accent/default', 'status/good/fg', 'status/pending/fg', 'status/warn/fg', 'status/critical/fg']) {
      const soft = colors[key.replace('/fg', '/soft')]
      const bases = key.startsWith('status') && soft ? [...backgrounds, over(soft, colors['surface/1'])] : backgrounds
      for (const bg of bases) expect(contrast(over(colors[key], bg), bg), key).toBeGreaterThanOrEqual(4.5)
    }
    for (const key of ['data/bar', 'status/warn/fill', 'focus/ring']) {
      for (const bg of backgrounds) expect(contrast(over(colors[key], bg), bg), key).toBeGreaterThanOrEqual(3)
    }
  })

  it('ships the same locally hosted fonts and licenses as HMI', () => {
    for (const name of ['Inter-latin.woff2', 'JetBrainsMono-latin.woff2', 'Inter-OFL.txt', 'JetBrainsMono-OFL.txt']) {
      expect(readFileSync(resolve('public/fonts', name))).toEqual(readFileSync(resolve('../hmi/public/fonts', name)))
    }
  })
})
