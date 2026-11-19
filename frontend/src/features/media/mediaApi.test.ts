import { describe, expect, it, vi } from 'vitest'
import { ApiError, setAccessToken } from '../../lib/api'
import { json, mockApi } from '../../test/utils'
import {
  mediaApi,
  mediaKeys,
  putToStorage,
  type DeedPhotosOut,
  type MediaOptions,
  type OwnerPhoto,
  type UploadTicket,
} from './mediaApi'

const OWNER_PHOTO: OwnerPhoto = {
  id: 'p1',
  position: 1,
  status: 'processing',
  thumb_url: null,
  display_url: null,
  blurred_count: 1,
  can_remove: true,
}

const TICKET: UploadTicket = {
  photo_id: 'p1',
  upload: {
    url: 'https://storage.test/gooddeeds/staging/p1?X-Amz-Signature=abc',
    method: 'PUT',
    headers: { 'Content-Type': 'image/jpeg', 'Content-Length': '4' },
  },
  expires_at: '2026-11-19T10:15:00Z',
}

const FILE = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: 'image/jpeg' })

describe('mediaApi', () => {
  it('loads options', async () => {
    const options: MediaOptions = {
      photos_enabled: true,
      photos_per_owner: 4,
      photo_edit_window_hours: 24,
      upload_allowed_types: ['image/jpeg', 'image/png', 'image/webp'],
      upload_max_bytes: 10_485_760,
      blur_max_regions: 8,
      decision_note_max: 1000,
      report_reasons: [{ id: 'child_no_guardian_consent', label: 'A child…', priority: 1 }],
    }
    mockApi({ 'GET /media/options': { body: options } })
    await expect(mediaApi.options()).resolves.toEqual(options)
  })

  it('creates, completes and discards an upload', async () => {
    const api = mockApi({
      'POST /media/uploads': { status: 201, body: TICKET },
      'POST /media/uploads/p1/complete': { body: { photo_id: 'p1', status: 'uploaded' } },
      'DELETE /media/uploads/p1': { status: 204 },
    })
    const request = { purpose: 'deed' as const, content_type: 'image/jpeg', size_bytes: 4 }
    await expect(mediaApi.createUpload(request)).resolves.toEqual(TICKET)
    expect(api.calls('POST /media/uploads')[0].body).toEqual(request)

    await expect(mediaApi.completeUpload('p1')).resolves.toEqual({
      photo_id: 'p1',
      status: 'uploaded',
    })
    await expect(mediaApi.discardUpload('p1')).resolves.toBeUndefined()
  })

  it.each([
    [400, 'unsupported_type'],
    [400, 'too_large'],
    [403, 'photos_disabled'],
    [429, 'too_many_pending_uploads'],
  ])('surfaces %i %s when creating an upload', async (status, code) => {
    mockApi({ 'POST /media/uploads': { status, body: { detail: 'x', code } } })
    await expect(
      mediaApi.createUpload({ purpose: 'self_log', content_type: 'image/gif', size_bytes: 1 }),
    ).rejects.toMatchObject({ status, code })
  })

  it('surfaces 409 upload_missing when completing', async () => {
    mockApi({
      'POST /media/uploads/p1/complete': {
        status: 409,
        body: { detail: 'x', code: 'upload_missing' },
      },
    })
    await expect(mediaApi.completeUpload('p1')).rejects.toMatchObject({ code: 'upload_missing' })
  })

  it('attaches photos with blur regions to a deed and a self-log', async () => {
    const api = mockApi({
      'POST /media/deeds/d1/photos': { body: { photos: [OWNER_PHOTO] } },
      'POST /media/self-logs/s1/photos': { body: { photos: [OWNER_PHOTO] } },
    })
    const deedBody = {
      photos: [{ photo_id: 'p1', blur_regions: [{ x: 0.4, y: 0.3, w: 0.2, h: 0.2 }] }],
    }
    await expect(mediaApi.attachToDeed('d1', deedBody)).resolves.toEqual({
      photos: [OWNER_PHOTO],
    })
    expect(api.calls('POST /media/deeds/d1/photos')[0].body).toEqual(deedBody)

    const logBody = { photos: [{ photo_id: 'p1', blur_regions: [] }] }
    await mediaApi.attachToSelfLog('s1', logBody)
    expect(api.calls('POST /media/self-logs/s1/photos')[0].body).toEqual(logBody)
  })

  it.each(['photo_window_closed', 'too_many_photos', 'photo_not_uploaded', 'deed_not_editable'])(
    'surfaces 409 %s when attaching',
    async (code) => {
      mockApi({ 'POST /media/deeds/d1/photos': { status: 409, body: { detail: 'x', code } } })
      await expect(
        mediaApi.attachToDeed('d1', { photos: [{ photo_id: 'p1', blur_regions: [] }] }),
      ).rejects.toMatchObject({ status: 409, code })
    },
  )

  it('reads owner photos with outcomes, including a doer removal', async () => {
    const body: DeedPhotosOut = {
      photos: [{ ...OWNER_PHOTO, status: 'ready', thumb_url: 't', display_url: 'd' }],
      can_add: true,
      add_until: '2026-11-20T08:00:00Z',
      outcomes: [
        {
          decision: 'removed_by_doer',
          removal_reason: null,
          removed_by: { handle: 'ahmed', display_name: 'Ahmed Khan' },
          decided_at: '2026-11-19T09:00:00Z',
          photo_count: 1,
        },
        {
          decision: 'removed',
          removal_reason: 'not_consented',
          removed_by: null,
          decided_at: '2026-11-19T08:30:00Z',
          photo_count: 1,
        },
      ],
    }
    mockApi({
      'GET /media/deeds/d1/photos': { body },
      'GET /media/self-logs/s1/photos': { body: { photos: [] } },
    })
    await expect(mediaApi.deedPhotos('d1')).resolves.toEqual(body)
    await expect(mediaApi.selfLogPhotos('s1')).resolves.toEqual({ photos: [] })
  })

  it('deletes a photo', async () => {
    const api = mockApi({ 'DELETE /media/photos/p1': { status: 204 } })
    await expect(mediaApi.deletePhoto('p1')).resolves.toBeUndefined()
    expect(api.calls('DELETE /media/photos/p1')).toHaveLength(1)
  })

  it('reports a photo and surfaces own_photo', async () => {
    const api = mockApi({
      'POST /media/photos/p1/reports': { status: 202, body: { status: 'received' } },
      'POST /media/photos/p2/reports': { status: 400, body: { detail: 'x', code: 'own_photo' } },
    })
    await expect(mediaApi.reportPhoto('p1', { reason: 'other' })).resolves.toEqual({
      status: 'received',
    })
    expect(api.calls('POST /media/photos/p1/reports')[0].body).toEqual({ reason: 'other' })
    await expect(
      mediaApi.reportPhoto('p2', { reason: 'not_consented', details: 'Me' }),
    ).rejects.toMatchObject({ status: 400, code: 'own_photo' })
  })

  it('keys everything under media', () => {
    expect(mediaKeys.options).toEqual(['media', 'options'])
    expect(mediaKeys.deed('d1')).toEqual(['media', 'deed', 'd1'])
    expect(mediaKeys.selfLog('s1')).toEqual(['media', 'self-log', 's1'])
  })
})

describe('putToStorage', () => {
  it('PUTs to the signed URL with only the signed headers (no token, device id or cookies)', async () => {
    setAccessToken('secret-access-token')
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 200 }))

    await putToStorage(TICKET, FILE)

    expect(spy).toHaveBeenCalledTimes(1)
    const [url, init] = spy.mock.calls[0]
    expect(url).toBe(TICKET.upload.url)
    expect(init?.method).toBe('PUT')
    expect(init?.body).toBe(FILE)
    expect(init?.credentials).toBe('omit')
    expect(init?.referrerPolicy).toBe('no-referrer')
    // Content-Length is set by the browser from the body; the rest goes exactly as signed.
    expect(init?.headers).toEqual({ 'Content-Type': 'image/jpeg' })
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBeNull()
    expect(headers.get('X-Device-Id')).toBeNull()
  })

  it('refuses a file whose size differs from the signed length, without calling storage', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    const bigger = new Blob([new Uint8Array(5)], { type: 'image/jpeg' })
    await expect(putToStorage(TICKET, bigger)).rejects.toMatchObject({ code: 'size_mismatch' })
    expect(spy).not.toHaveBeenCalled()
  })

  it('throws upload_failed when storage refuses the PUT', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(403, { message: 'SignatureDoesNotMatch' }))
    const error = await putToStorage(TICKET, FILE).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 403, code: 'upload_failed' })
  })

  it('throws upload_failed when the network drops', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(putToStorage(TICKET, FILE)).rejects.toMatchObject({ code: 'upload_failed' })
  })

  it('passes an abort through unchanged', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new DOMException('Aborted', 'AbortError'))
    const controller = new AbortController()
    await expect(putToStorage(TICKET, FILE, { signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    })
  })
})
