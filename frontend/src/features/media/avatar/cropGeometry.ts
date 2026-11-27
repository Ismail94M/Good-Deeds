/**
 * The square crop of the avatar editor (§6.3 step 3). The state is the crop's centre as fractions
 * of the image width and height, plus a zoom: at zoom 1 the square's side is the image's shorter
 * side. The API wants the top-left corner as fractions of the width and height and the side as a
 * fraction of the shorter side (`AvatarCropIn`), so pixels outside it are never stored.
 */

import type { AvatarCrop } from './avatarApi'

export type CropState = { cx: number; cy: number; zoom: number }
export type ImageSize = { width: number; height: number }

/** `avatar_min_side_px` (D116). */
export const AVATAR_MIN_SIDE = 256
/** Never zoom in further than this. */
export const MAX_ZOOM = 4

/** Zoom limit: up to 4x, but the kept square must stay at least 256 px. */
export function maxZoom(image: ImageSize, minSide: number = AVATAR_MIN_SIDE): number {
  const short = Math.min(image.width, image.height)
  return Math.max(1, Math.min(MAX_ZOOM, short / minSide))
}

/** The crop's side in image pixels. */
export function sidePx(image: ImageSize, zoom: number): number {
  return Math.min(image.width, image.height) / zoom
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value))
}

/** Keep the zoom in range and the square inside the image. */
export function clampCrop(state: CropState, image: ImageSize, minSide?: number): CropState {
  const zoom = clamp(state.zoom, 1, maxZoom(image, minSide))
  const side = sidePx(image, zoom)
  const halfX = side / 2 / image.width
  const halfY = side / 2 / image.height
  return {
    zoom,
    cx: clamp(state.cx, halfX, 1 - halfX),
    cy: clamp(state.cy, halfY, 1 - halfY),
  }
}

/** The centred square at zoom 1. */
export function initialCrop(image: ImageSize): CropState {
  return clampCrop({ cx: 0.5, cy: 0.5, zoom: 1 }, image)
}

/** Move the square by a drag of (dx, dy) screen pixels on a frame `frame` pixels wide. */
export function panBy(
  state: CropState,
  image: ImageSize,
  dx: number,
  dy: number,
  frame: number,
): CropState {
  if (frame <= 0) return state
  const side = sidePx(image, state.zoom)
  // Dragging the picture right shows more of its left side: the square moves left.
  const moveX = (-dx / frame) * side
  const moveY = (-dy / frame) * side
  return clampCrop(
    { ...state, cx: state.cx + moveX / image.width, cy: state.cy + moveY / image.height },
    image,
  )
}

/** Change the zoom around the current centre. */
export function zoomTo(state: CropState, image: ImageSize, zoom: number): CropState {
  return clampCrop({ ...state, zoom }, image)
}

/** The request body's crop (P2). */
export function toApiCrop(state: CropState, image: ImageSize): AvatarCrop {
  const short = Math.min(image.width, image.height)
  const side = sidePx(image, state.zoom)
  const round = (n: number) => Math.round(clamp(n, 0, 1) * 1e6) / 1e6
  return {
    x: round((state.cx * image.width - side / 2) / image.width),
    y: round((state.cy * image.height - side / 2) / image.height),
    size: round(side / short),
  }
}

/**
 * How to draw the image inside a square frame so the crop fills it exactly, as CSS percentages
 * of the frame: works for the editor and every preview size alike.
 */
export function imageStyle(
  state: CropState,
  image: ImageSize,
): { width: string; height: string; left: string; top: string } {
  const side = sidePx(image, state.zoom)
  const pct = (n: number) => `${(n * 100).toFixed(4)}%`
  return {
    width: pct(image.width / side),
    height: pct(image.height / side),
    left: pct(-(state.cx * image.width - side / 2) / side),
    top: pct(-(state.cy * image.height - side / 2) / side),
  }
}
