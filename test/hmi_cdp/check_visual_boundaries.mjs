// Compare visual batches with the pre-implementation source without checking out/resetting the shared tree.
import ts from '../../hmi/node_modules/typescript/lib/typescript.js'
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'

const root = path.resolve(import.meta.dirname, '../..')
const base = '92e1332b1073b8b8deb7df18371092181e9079d5'
const batch = process.argv[2] || 'i2'
assert.match(batch, /^(i[1-6]|followup-(types|visual))$/)
const git = args => execFileSync('git', args, { cwd: root, encoding: 'utf8' })
const normalize = text => text.replace(/\r\n/g, '\n')
const tracked = new Set(git(['ls-tree', '-r', '--name-only', base, 'hmi/src']).trim().split('\n'))
const changed = new Map(git(['diff', '--name-only', base, '--', 'hmi/src']).trim().split('\n')
  .filter(file => tracked.has(file)).map(file => [path.resolve(root, file), git(['show', `${base}:${file}`])]))
const configFile = path.join(root, 'hmi/tsconfig.json')
const config = ts.parseJsonConfigFileContent(ts.readConfigFile(configFile, ts.sys.readFile).config, ts.sys, path.join(root, 'hmi'))

function handler(text) {
  const source = ts.createSourceFile('App.tsx', normalize(text), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let result
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'handleEvent') result = node.getText(source)
    ts.forEachChild(node, visit)
  }
  visit(source)
  assert.ok(result?.length > 100, 'handleEvent was not found')
  return result
}
const app = readFileSync(path.join(root, 'hmi/src/App.tsx'), 'utf8')
const originalHandler = handler(git(['show', `${base}:hmi/src/App.tsx`]))
assert.equal(handler(app), originalHandler)
// Negative control: the scan must reject a missing handler.
assert.throws(() => handler(app.replace('const handleEvent =', 'const missingHandler =')))
assert.equal(normalize(readFileSync(path.join(root, 'hmi/src/types.ts'), 'utf8')), normalize(git(['show', `${base}:hmi/src/types.ts`])))

function diagnostics(baseline) {
  const host = ts.createCompilerHost(config.options)
  const read = host.readFile
  const exists = host.fileExists
  const relative = file => path.relative(root, file).split(path.sep).join('/')
  host.fileExists = file => baseline && relative(file).startsWith('hmi/src/') ? tracked.has(relative(file)) : exists(file)
  host.readFile = file => baseline && relative(file).startsWith('hmi/src/') && !tracked.has(relative(file)) ? undefined
    : baseline && changed.has(path.resolve(file)) ? changed.get(path.resolve(file)) : read(file)
  const files = baseline ? config.fileNames.filter(file => tracked.has(path.relative(root, file).split(path.sep).join('/'))) : config.fileNames
  const program = ts.createProgram(files, config.options, host)
  return ts.getPreEmitDiagnostics(program).map(d => ({ file: d.file && path.relative(root, d.file.fileName), code: d.code,
    message: ts.flattenDiagnosticMessageText(d.messageText, ' ') })).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
}
const before = diagnostics(true), after = diagnostics(false)
const out = path.join(root, `.artifacts/hmi-visual-v2/${batch}`)
mkdirSync(out, { recursive: true })
writeFileSync(path.join(out, 'type-comparison.json'), JSON.stringify({ base, before, after, unchangedHandler: true, unchangedTypes: true }, null, 2))
const available = before.map(d => JSON.stringify(d))
for (const diagnostic of after) {
  const at = available.indexOf(JSON.stringify(diagnostic))
  assert.ok(at >= 0, 'New TypeScript diagnostic: ' + JSON.stringify(diagnostic))
  available.splice(at, 1)
}
console.log(`PASS: handleEvent and types.ts unchanged; TypeScript baseline ${before.length}, current ${after.length}, no new errors (${after.length ? 'not a clean typecheck' : 'full typecheck passed'})`)
