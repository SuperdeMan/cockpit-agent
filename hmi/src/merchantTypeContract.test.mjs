import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import ts from 'typescript'

const fixture = fileURLToPath(new URL('./merchantTypeContract.fixture.ts', import.meta.url))

test('merchant payment and cancellation fixtures satisfy the declared UI card types', () => {
  const program = ts.createProgram({
    rootNames: [fixture],
    options: {
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
      strict: true,
      skipLibCheck: true,
      noEmit: true,
    },
  })
  const diagnostics = ts.getPreEmitDiagnostics(program)
    .filter((diagnostic) => diagnostic.file
      && path.resolve(diagnostic.file.fileName) === path.resolve(fixture))
    .map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'))

  assert.deepEqual(diagnostics, [])
})

test('new merchant rendering paths do not erase their card types with any', () => {
  const cards = readFileSync(new URL('./components/Cards.tsx', import.meta.url), 'utf8')
  const chat = readFileSync(new URL('./components/ChatView.tsx', import.meta.url), 'utf8')

  assert.doesNotMatch(cards, /function PaymentQrCardView\([^\n]+card:\s*any/)
  assert.doesNotMatch(cards, /function McpOrderCardView\([^\n]+card:\s*any/)
  assert.doesNotMatch(chat, /msg\.uiCard\s+as\s+any/)
})
