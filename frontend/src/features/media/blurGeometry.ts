/**
 * Blur circles for the blur editor (docs/M3-DESIGN.md §4.5, §6.3). A circle is kept as its centre
 * (fractions of the image) and its diameter as a fraction of the image's shorter side, so it stays
 * round on screen whatever the photo's shape. It is sent as an ellipse `{x, y, w, h}` in fractions
 * of the displayed, orientation-corrected image (top-left origin).
 */

import type { BlurRegion } from './mediaApi'

export type Circle = { cx: number; cy: number; d: number }

/**
 * Smallest side of a region as a fraction of the image. Above the server's `blur_min_region`
 * (0.02; not in `/media/options`), so a circle the editor allows is never refused.
 */
export const MIN_REGION = 0.05
export const DEFAULT_DIAMETER = 0.3
export const MOVE_STEP = 0.02
export const MOVE_STEP_LARGE = 0.1
export const SIZE_STEP = 0.05

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

/** The smallest diameter that keeps both sides of the region at least `MIN_REGION`. */
export function minDiameter(width: number, height: number): number {
  return Math.min(1, (MIN_REGION * Math.max(width, height)) / Math.min(width, height))
}

function sides(d: number, width: number, height: number) {
  const short = Math.min(width, height)
  return { w: (d * short) / width, h: (d * short) / height }
}

/** Keep the circle's size within limits and the whole circle inside the image. */
export function clampCircle(circle: Circle, width: number, height: number): Circle {
  const d = clamp(circle.d, minDiameter(width, height), 1)
  const { w, h } = sides(d, width, height)
  return {
    d,
    cx: clamp(circle.cx, w / 2, 1 - w / 2),
    cy: clamp(circle.cy, h / 2, 1 - h / 2),
  }
}

export function newCircle(width: number, height: number): Circle {
  return clampCircle({ cx: 0.5, cy: 0.5, d: DEFAULT_DIAMETER }, width, height)
}

const round = (value: number) => Math.round(value * 10_000) / 10_000

/** The region sent to the API: inside [0, 1] with `x + w ≤ 1` and `y + h ≤ 1` after rounding. */
export function toRegion(circle: Circle, width: number, height: number): BlurRegion {
  const { w, h } = sides(circle.d, width, height)
  const rw = Math.min(1, round(w))
  const rh = Math.min(1, round(h))
  return {
    x: clamp(round(circle.cx - w / 2), 0, round(1 - rw)),
    y: clamp(round(circle.cy - h / 2), 0, round(1 - rh)),
    w: rw,
    h: rh,
  }
}

export function fromRegion(region: BlurRegion, width: number, height: number): Circle {
  const short = Math.min(width, height)
  return clampCircle(
    { cx: region.x + region.w / 2, cy: region.y + region.h / 2, d: (region.w * width) / short },
    width,
    height,
  )
}
