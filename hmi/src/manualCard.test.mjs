import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_MANUAL_IMAGE_URI_CHARS,
  manualImages,
  manualImageUri,
} from './manualCard.mjs'

test('manual image accepts only bounded PNG/JPEG base64 data URIs', () => {
  assert.equal(manualImageUri('data:image/png;base64,eA=='), 'data:image/png;base64,eA==')
  assert.equal(manualImageUri('data:image/jpeg;base64,/9g='), 'data:image/jpeg;base64,/9g=')
  assert.equal(manualImageUri('https://example.com/manual.png'), '')
  assert.equal(manualImageUri('data:image/svg+xml;base64,eA=='), '')
  assert.equal(manualImageUri('data:image/png;base64,not valid'), '')
  assert.equal(manualImageUri(`data:image/png;base64,${'A'.repeat(MAX_MANUAL_IMAGE_URI_CHARS)}`), '')
})

test('manual card keeps at most two valid unique images', () => {
  const images = manualImages({ images: [
    { asset_id: 'a', data_uri: 'data:image/png;base64,eA==' },
    { asset_id: 'a', data_uri: 'data:image/png;base64,eA==' },
    { asset_id: 'bad', data_uri: 'http://bad.invalid/x.png' },
    { asset_id: 'b', data_uri: 'data:image/jpeg;base64,/9g=' },
    { asset_id: 'c', data_uri: 'data:image/png;base64,eA==' },
  ] })

  assert.deepEqual(images.map((item) => item.asset_id), ['a', 'b'])
})
