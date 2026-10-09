// @vitest-environment node
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { expect, test } from 'vitest'

const repo = path.resolve('..')
const recipe = readFileSync(path.resolve('Dockerfile'), 'utf8')

function imageLocation(source: string, dockerfile: string): string | null {
  let cwd = '/', destination: string | null = null
  for (const line of dockerfile.split(/\r?\n/)) {
    const [instruction, ...words] = line.trim().split(/\s+/)
    if (instruction === 'WORKDIR') cwd = path.posix.resolve(cwd, words[0])
    if (instruction !== 'COPY') continue
    const target = path.posix.resolve(cwd, words[words.length - 1])
    const sources = words.slice(0, -1)
    for (const from of sources) {
      if (from.endsWith('/') && source.startsWith(from)) destination = path.posix.join(target, source.slice(from.length))
      if (from === source) destination = words[words.length - 1].endsWith('/') || sources.length > 1
        ? path.posix.join(target, path.posix.basename(source)) : target
    }
  }
  return destination
}

function checkRuntimeImports(dockerfile: string) {
  const pending = ['dashboard/src/main.tsx'], visited = new Set<string>()
  while (pending.length) {
    const source = pending.pop()!
    if (visited.has(source)) continue
    visited.add(source)
    const at = imageLocation(source, dockerfile)
    if (!at) throw new Error('missing image source: ' + source)
    if (!/\.[jt]sx?$/.test(source)) continue
    const imports = ts.preProcessFile(readFileSync(path.join(repo, source), 'utf8')).importedFiles
    for (const imported of imports) {
      if (!imported.fileName.startsWith('.')) continue
      const stem = path.posix.normalize(path.posix.join(path.posix.dirname(source), imported.fileName))
      const resolved = [stem, ...['.ts', '.tsx', '.js', '.mjs', '/index.ts', '/index.tsx'].map(extension => stem + extension)]
        .find(candidate => existsSync(path.join(repo, candidate)) && /\.[a-z]+$/.test(candidate))
      if (!resolved) throw new Error('unresolved source import: ' + stem)
      const expected = path.posix.normalize(path.posix.join(path.posix.dirname(at), path.posix.relative(path.posix.dirname(source), resolved)))
      if (imageLocation(resolved, dockerfile) !== expected) throw new Error('missing or misplaced image import: ' + resolved)
      pending.push(resolved)
    }
  }
  return visited
}

test('the Docker COPY layout contains the runtime relative-import closure', () => {
  const files = checkRuntimeImports(recipe)
  expect(files.has('hmi/src/components/icons.gen.ts')).toBe(true)
  expect(files.has('hmi/src/components/icons.custom.ts')).toBe(true)
  expect(files.has('mobile/src/ui/icons.local.ts')).toBe(true)
})
test('missing shared icon copies fail the same import-closure check', () => {
  expect(() => checkRuntimeImports(recipe.replace(/^COPY (?:hmi|mobile)\/.*\r?\n/gm, ''))).toThrow(/image import/)
})
test('flattening the dashboard to the old workdir fails relative resolution', () => {
  expect(() => checkRuntimeImports(recipe.replace('WORKDIR /app/dashboard', 'WORKDIR /app'))).toThrow(/image import/)
})
