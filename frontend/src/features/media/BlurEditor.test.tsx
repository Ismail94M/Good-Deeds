/** F15: the blur editor (docs/M3-DESIGN.md §4.5, §6.3): add, drag, resize, remove, Cancel, Done. */

import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fitWithin } from './resizeImage'
import {
  clampCircle,
  fromRegion,
  minDiameter,
  MIN_REGION,
  newCircle,
  toRegion,
} from './blurGeometry'
import { BlurEditor } from './BlurEditor'
import type { BlurRegion } from './mediaApi'

const W = 1600
const H = 1200

function renderEditor(initial: BlurRegion[] = [], maxRegions = 3) {
  const onDone = vi.fn()
  const onCancel = vi.fn()
  render(
    <BlurEditor
      imageUrl="blob:preview-1"
      width={W}
      height={H}
      initial={initial}
      maxRegions={maxRegions}
      onCancel={onCancel}
      onDone={onDone}
    />,
  )
  return { onDone, onCancel, user: userEvent.setup() }
}

function expectRegions(onDone: ReturnType<typeof vi.fn>, expected: BlurRegion[]) {
  const regions = onDone.mock.calls[0][0] as BlurRegion[]
  expect(regions).toHaveLength(expected.length)
  regions.forEach((region, i) => {
    for (const key of ['x', 'y', 'w', 'h'] as const) {
      expect(region[key]).toBeCloseTo(expected[i][key], 3)
    }
  })
}

/** The stage is 400 × 300 px on screen. */
function sizeStage() {
  const stage = screen.getByRole('img', { name: 'The photo you’re blurring' }).parentElement!
  vi.spyOn(stage, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 400,
    bottom: 300,
    width: 400,
    height: 300,
    toJSON: () => ({}),
  })
  return stage
}

describe('blur geometry', () => {
  it('keeps a circle round as an ellipse of fractions and inside the image', () => {
    expect(toRegion(newCircle(W, H), W, H)).toEqual({ x: 0.3875, y: 0.35, w: 0.225, h: 0.3 })
    const corner = clampCircle({ cx: 2, cy: -1, d: 0.5 }, W, H)
    const region = toRegion(corner, W, H)
    expect(region.x + region.w).toBeLessThanOrEqual(1)
    expect(region.y).toBe(0)
  })

  it('never goes below the minimum region or above the short side', () => {
    const tiny = toRegion(clampCircle({ cx: 0.5, cy: 0.5, d: 0 }, W, H), W, H)
    expect(Math.min(tiny.w, tiny.h)).toBeGreaterThanOrEqual(MIN_REGION)
    expect(minDiameter(W, H)).toBeCloseTo((MIN_REGION * W) / H)
    expect(clampCircle({ cx: 0.5, cy: 0.5, d: 3 }, W, H).d).toBe(1)
  })

  it('round-trips a region', () => {
    const region = { x: 0.1, y: 0.2, w: 0.15, h: 0.2 }
    expect(toRegion(fromRegion(region, W, H), W, H)).toEqual(region)
  })

  it('fits images within 2048 px without upscaling', () => {
    expect(fitWithin(4032, 3024)).toEqual({ width: 2048, height: 1536 })
    expect(fitWithin(3024, 4032)).toEqual({ width: 1536, height: 2048 })
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 })
  })
})

describe('BlurEditor', () => {
  it('opens as a dialog with one centred circle and the canvas copy', () => {
    renderEditor()
    expect(screen.getByRole('dialog', { name: 'Blur a face (optional)' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Drag the circle over the face of anyone who’d rather not be shown. Everyone else stays exactly as they are.',
      ),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Blur \d of \d$/ })).toHaveLength(1)
    expect(
      screen.getByText(
        'Only the blurred version is posted. You can change this until you send the report.',
      ),
    ).toBeInTheDocument()
  })

  it('moves with arrow keys and resizes with + and −, then returns fractions on Done', async () => {
    const { onDone, user } = renderEditor()
    const circle = screen.getByRole('button', { name: 'Blur 1 of 1' })
    circle.focus()
    await user.keyboard('{ArrowRight}{ArrowRight}{ArrowDown}')
    await user.keyboard('+')
    await user.click(screen.getByRole('button', { name: 'Done' }))
    // Centre (0.54, 0.52), diameter 0.35 of the short side (1200 px).
    expectRegions(onDone, [{ x: 0.40875, y: 0.345, w: 0.2625, h: 0.35 }])
  })

  it('drags with a pointer and resizes from the corner handle', async () => {
    const { onDone, user } = renderEditor()
    const stage = sizeStage()
    const circle = screen.getByRole('button', { name: 'Blur 1 of 1' })
    fireEvent.pointerDown(circle, { pointerId: 1, clientX: 200, clientY: 150 })
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 240, clientY: 180 })
    fireEvent.pointerUp(stage, { pointerId: 1 })
    // Moved by 40 / 400 and 30 / 300: centre (0.6, 0.6).
    const handle = screen.getByTestId('blur-resize-0')
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 300, clientY: 250 })
    fireEvent.pointerMove(stage, { pointerId: 2, clientX: 315, clientY: 265 })
    fireEvent.pointerUp(stage, { pointerId: 2 })
    // Diameter grows by (15 + 15) / 300 = 0.1 → 0.4.
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expectRegions(onDone, [{ x: 0.45, y: 0.4, w: 0.3, h: 0.4 }])
  })

  it('adds blurs up to the limit and removes the selected one', async () => {
    const { onDone, user } = renderEditor([], 2)
    const add = screen.getByRole('button', { name: 'Add a blur' })
    await user.click(add)
    expect(screen.getAllByRole('button', { name: /^Blur \d of 2$/ })).toHaveLength(2)
    expect(add).toBeDisabled()
    // The new circle is focused and selected.
    expect(screen.getByRole('button', { name: 'Blur 2 of 2' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Remove this blur' }))
    expect(screen.getAllByRole('button', { name: /^Blur \d of 1$/ })).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(onDone.mock.calls[0][0]).toHaveLength(1)
  })

  it('starts from the existing regions when editing', async () => {
    const region = { x: 0.1, y: 0.2, w: 0.15, h: 0.2 }
    const { onDone, user } = renderEditor([region])
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(onDone).toHaveBeenCalledWith([region])
  })

  it('Cancel and Esc discard changes', async () => {
    const { onCancel, onDone, user } = renderEditor()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(2)
    expect(onDone).not.toHaveBeenCalled()
  })
})
