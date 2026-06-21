import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')

test('defines daylight surfaces for card interiors and semantic AQI badges', () => {
  assert.match(css, /:root\[data-theme='light'\]\s*\{[\s\S]*--weather-surface:/)
  assert.match(css, /:root\[data-theme='light'\]\s*\{[\s\S]*--card-inset-surface:/)
  assert.match(css, /:root\[data-theme='light'\]\s*\.air-badge-excellent\s*\{[\s\S]*--air-color:/)
  assert.match(css, /:root\[data-theme='light'\]\s*\.stock-up\s+\.card-stock-price/)
})

test('gives conclusion-style news and search cards component-level styling', () => {
  for (const selector of [
    '.card-news-digest', '.news-digest-summary', '.card-search-answer', '.sources-toggle',
  ]) {
    assert.ok(css.includes(selector), `missing ${selector}`)
  }
})
