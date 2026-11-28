import { describe, expect, it } from 'vitest'
import {
  clampCrop,
  imageStyle,
  initialCrop,
  maxZoom,
  panBy,
  toApiCrop,
  zoomTo,
} from './cropGeometry'

const LANDSCAPE = { width: 2000, height: 1000 }
const PORTRAIT = { width: 800, height: 1600 }

describe('avatar crop geometry', () => {
  it('starts with the centred square of the shorter side', () => {
    expect(toApiCrop(initialCrop(LANDSCAPE), LANDSCAPE)).toEqual({ x: 0.25, y: 0, size: 1 })
    expect(toApiCrop(initialCrop(PORTRAIT), PORTRAIT)).toEqual({ x: 0, y: 0.25, size: 1 })
  })

  it('keeps the square inside the image when dragged past an edge', () => {
    const start = initialCrop(LANDSCAPE)
    // Drag the picture far to the right: the square reaches the left edge and stops.
    const moved = panBy(start, LANDSCAPE, 10_000, 0, 300)
    expect(toApiCrop(moved, LANDSCAPE)).toEqual({ x: 0, y: 0, size: 1 })
    const back = panBy(start, LANDSCAPE, -10_000, 0, 300)
    expect(toApiCrop(back, LANDSCAPE)).toEqual({ x: 0.5, y: 0, size: 1 })
  })

  it('moves by frame pixels scaled to the image', () => {
    // At zoom 1 the 300 px frame shows 1000 image px; a 30 px drag moves 100 image px.
    const moved = panBy(initialCrop(LANDSCAPE), LANDSCAPE, -30, 0, 300)
    expect(toApiCrop(moved, LANDSCAPE).x).toBeCloseTo(0.3)
  })

  it('zooms in up to 4x but never below a 256 px square', () => {
    expect(maxZoom({ width: 4000, height: 3000 })).toBe(4)
    expect(maxZoom({ width: 512, height: 600 })).toBe(2)
    expect(maxZoom({ width: 256, height: 256 })).toBe(1)
    const zoomed = zoomTo(initialCrop(LANDSCAPE), LANDSCAPE, 10)
    expect(zoomed.zoom).toBeCloseTo(1000 / 256)
    const crop = toApiCrop(zoomed, LANDSCAPE)
    expect(crop.size * 1000).toBeCloseTo(256, 0)
    // Still centred.
    expect(crop.x + (crop.size * 1000) / 2 / 2000).toBeCloseTo(0.5)
    expect(clampCrop({ cx: 0.5, cy: 0.5, zoom: 0.2 }, LANDSCAPE).zoom).toBe(1)
  })

  it('draws the image so the crop fills the frame', () => {
    const style = imageStyle(initialCrop(LANDSCAPE), LANDSCAPE)
    expect(style).toEqual({
      width: '200.0000%',
      height: '100.0000%',
      left: '-50.0000%',
      top: '0.0000%',
    })
  })
})
