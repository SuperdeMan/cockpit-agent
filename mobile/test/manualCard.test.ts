import { manualImages, manualImageUri } from '@shared/manualCard.mjs'

describe('manual card image guard', () => {
  test('rejects remote and SVG images', () => {
    expect(manualImageUri('https://example.com/a.png')).toBe('')
    expect(manualImageUri('data:image/svg+xml;base64,eA==')).toBe('')
  })

  test('deduplicates and caps trusted inline images', () => {
    const images = manualImages({ images: [
      { asset_id: 'a', data_uri: 'data:image/png;base64,eA==' },
      { asset_id: 'a', data_uri: 'data:image/png;base64,eA==' },
      { asset_id: 'b', data_uri: 'data:image/jpeg;base64,/9g=' },
      { asset_id: 'c', data_uri: 'data:image/png;base64,eA==' },
    ] })
    expect(images.map((image) => image.asset_id)).toEqual(['a', 'b'])
  })
})
