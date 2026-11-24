import { describe, expect, it } from 'vitest'
import { ApiError } from '../../lib/api'
import { mockApi } from '../../test/utils'
import {
  deedsApi,
  deedsKeys,
  errorDoer,
  type DeedOptions,
  type DeedOut,
  type InviteOut,
  type ScanOut,
  type SelfLogOut,
} from './deedsApi'

const LOCATION = { lat: 12.97, lng: 77.59, accuracy_m: 20 }
const SCAN: ScanOut = {
  scan_id: 'scan-1',
  doer: { handle: 'ravi', display_name: 'Ravi' },
  expires_at: '2026-11-15T10:15:00Z',
  scanned_at: '2026-11-15T10:00:00Z',
  pair_reports_remaining: 3,
  month_resets_at: '2026-12-01T00:00:00+05:30',
  area_name: 'Koramangala',
}

const INVITE: InviteOut = {
  id: 'inv-1',
  deed_id: 'deed-2',
  status: 'pending',
  sent_via: 'whatsapp',
  created_at: '2026-11-15T10:00:00Z',
  expires_at: '2026-11-22T10:00:00Z',
  accepted_at: null,
  url: 'http://localhost:5173/invite/raw-token',
}

const DEED: DeedOut = {
  id: 'deed-1',
  status: 'verified',
  verification_method: 'qr_scan',
  category: 'animals',
  description: 'Fed a stray dog',
  beneficiaries_count: 1,
  occurred_at: '2026-11-15T10:00:00Z',
  reported_at: '2026-11-15T10:01:00Z',
  verified_at: '2026-11-15T10:01:00Z',
  withdrawn_at: null,
  last_edited_at: null,
  reporter: { handle: 'asha', display_name: 'Asha' },
  doer: { handle: 'ravi', display_name: 'Ravi' },
  same_act: { report_count: 1, counts_for_points: true },
  prize_rank_eligible: true,
  has_location: true,
  area_name: 'Koramangala',
  can_edit: true,
  can_withdraw: true,
  invite: null,
}

const SELF_LOG: SelfLogOut = {
  id: 'log-1',
  category: 'environment',
  description: 'Picked up litter',
  occurred_at: '2026-11-14T08:00:00Z',
  has_location: false,
  created_at: '2026-11-14T09:00:00Z',
  updated_at: '2026-11-14T09:00:00Z',
}

/** The full URL fetch was called with for request `n` (mockApi strips the query string). */
function urlOf(api: ReturnType<typeof mockApi>, n: number): string {
  return String(api.spy.mock.calls[n][0])
}

describe('deedsApi: scans', () => {
  it('creates a scan from a QR token', async () => {
    const api = mockApi({ 'POST /scans': { status: 201, body: SCAN } })
    await expect(deedsApi.createScan({ token: 'T.1.sig', location: LOCATION })).resolves.toEqual(
      SCAN,
    )
    expect(api.calls('POST /scans')[0].body).toEqual({ token: 'T.1.sig', location: LOCATION })
  })

  it('creates a scan from an ID and manual code', async () => {
    const api = mockApi({ 'POST /scans': { status: 201, body: SCAN } })
    await deedsApi.createScan({ short_id: 'GD-7K3M9Q0', code: '482913', location: LOCATION })
    expect(api.calls('POST /scans')[0].body).toEqual({
      short_id: 'GD-7K3M9Q0',
      code: '482913',
      location: LOCATION,
    })
  })

  it('surfaces the error code for a refused scan', async () => {
    mockApi({
      'POST /scans': {
        status: 404,
        body: { detail: "ID and code don't match.", code: 'doer_code_invalid' },
      },
    })
    const error = await deedsApi
      .createScan({ short_id: 'GD-7K3M9Q0', code: '000000', location: LOCATION })
      .catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('doer_code_invalid')
    expect((error as ApiError).status).toBe(404)
  })

  it('fetches a scan by id, including consumed', async () => {
    const api = mockApi({ 'GET /scans/scan-1': { body: { ...SCAN, consumed: false } } })
    await expect(deedsApi.scan('scan-1')).resolves.toEqual({ ...SCAN, consumed: false })
    expect(api.calls('GET /scans/scan-1')).toHaveLength(1)
  })

  it('surfaces 429 pair_monthly_limit with the doer from the body (D27)', async () => {
    const doer = { handle: 'ahmed', display_name: 'Ahmed Khan' }
    mockApi({
      'POST /scans': {
        status: 429,
        body: { detail: 'Monthly limit for this person.', code: 'pair_monthly_limit', doer },
      },
    })
    const error = await deedsApi
      .createScan({ token: 'T.1.sig', location: LOCATION })
      .catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 429, code: 'pair_monthly_limit' })
    expect(errorDoer(error)).toEqual(doer)
  })

  it('surfaces 429 pair_monthly_limit from deed create without a doer', async () => {
    mockApi({
      'POST /deeds': {
        status: 429,
        body: { detail: 'Monthly limit for this person.', code: 'pair_monthly_limit' },
      },
    })
    const error = await deedsApi
      .createDeed({ method: 'qr_scan', scan_id: 'scan-1', category: 'animals', description: 'x' })
      .catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 429, code: 'pair_monthly_limit' })
    expect(errorDoer(error)).toBeNull()
  })

  it('surfaces 410 scan_expired', async () => {
    mockApi({
      'GET /scans/scan-1': { status: 410, body: { detail: 'Scan again.', code: 'scan_expired' } },
    })
    await expect(deedsApi.scan('scan-1')).rejects.toMatchObject({
      status: 410,
      code: 'scan_expired',
    })
  })
})

describe('deedsApi: deeds', () => {
  it('submits a QR deed', async () => {
    const api = mockApi({ 'POST /deeds': { status: 201, body: DEED } })
    const body = {
      method: 'qr_scan' as const,
      scan_id: 'scan-1',
      category: 'animals',
      description: 'Fed a stray dog',
      beneficiaries_count: 1,
    }
    await expect(deedsApi.createDeed(body)).resolves.toEqual(DEED)
    expect(api.calls('POST /deeds')[0].body).toEqual(body)
  })

  it('submits an invite deed without a location and returns the one-time url', async () => {
    const pending: DeedOut = {
      ...DEED,
      id: 'deed-2',
      status: 'pending',
      verification_method: 'invite_accept',
      verified_at: null,
      doer: null,
      same_act: null,
      prize_rank_eligible: false,
      has_location: false,
      invite: INVITE,
    }
    const api = mockApi({ 'POST /deeds': { status: 201, body: pending } })
    const body = {
      method: 'invite' as const,
      category: 'food_sharing',
      description: 'Shared lunch with a guard',
      beneficiaries_count: 2,
      occurred_at: '2026-11-14T13:00:00Z',
      location: null,
      sent_via: 'whatsapp' as const,
    }
    const deed = await deedsApi.createDeed(body)
    expect(api.calls('POST /deeds')[0].body).toEqual(body)
    expect(deed.doer).toBeNull()
    expect(deed.invite?.url).toBe(INVITE.url)
  })

  it('surfaces 429 invite_daily_limit with its code', async () => {
    mockApi({
      'POST /deeds': {
        status: 429,
        body: { detail: 'You have sent all your invites for today.', code: 'invite_daily_limit' },
      },
    })
    await expect(
      deedsApi.createDeed({
        method: 'invite',
        category: 'other',
        description: 'Helped someone',
        occurred_at: '2026-11-14T13:00:00Z',
        sent_via: 'copy_link',
      }),
    ).rejects.toMatchObject({ status: 429, code: 'invite_daily_limit' })
  })

  it('gets, edits and withdraws a deed', async () => {
    const api = mockApi({
      'GET /deeds/deed-1': { body: DEED },
      'PATCH /deeds/deed-1': {
        body: { ...DEED, description: 'Fed two stray dogs', last_edited_at: 'x' },
      },
      'POST /deeds/deed-1/withdraw': {
        body: { ...DEED, status: 'withdrawn', can_edit: false, can_withdraw: false },
      },
    })

    await expect(deedsApi.deed('deed-1')).resolves.toEqual(DEED)

    const edited = await deedsApi.updateDeed('deed-1', { description: 'Fed two stray dogs' })
    expect(edited.description).toBe('Fed two stray dogs')
    expect(api.calls('PATCH /deeds/deed-1')[0].body).toEqual({ description: 'Fed two stray dogs' })

    const withdrawn = await deedsApi.withdrawDeed('deed-1')
    expect(withdrawn.status).toBe('withdrawn')
    expect(api.calls('POST /deeds/deed-1/withdraw')[0].body).toBeUndefined()
  })

  it('loads deed options with the Revision 5 limits', async () => {
    const options: DeedOptions = {
      categories: [{ slug: 'animals', label: 'Animals' }],
      invites_enabled: false,
      invites_remaining_today: 3,
      invite_expiry_hours: 168,
      reports_remaining_this_month: 12,
      reporter_monthly_limit: 150,
      reporter_doer_monthly_limit: 15,
      reporter_monthly_warn_at: 10,
      reporter_doer_monthly_warn_at: 3,
      month_resets_at: '2026-12-01T00:00:00+05:30',
      description_min: 10,
      description_max: 1000,
      beneficiaries_max: 10000,
      scan_submit_minutes: 15,
      qr_occurred_max_age_minutes: 120,
      max_location_accuracy_m: 500,
      service_area_radius_km: 40,
    }
    mockApi({ 'GET /deeds/options': { body: options } })
    await expect(deedsApi.options()).resolves.toEqual(options)
  })

  it('lists my deeds by role with cursor and limit in the query', async () => {
    const page = { items: [DEED], next_cursor: 'c2' }
    const api = mockApi({ 'GET /me/deeds': { body: page } })

    await expect(deedsApi.myDeeds({ role: 'reporter' })).resolves.toEqual(page)
    expect(urlOf(api, 0)).toBe('/api/me/deeds?role=reporter')

    await deedsApi.myDeeds({ role: 'doer', cursor: 'c2', limit: 20 })
    expect(urlOf(api, 1)).toBe('/api/me/deeds?role=doer&cursor=c2&limit=20')
  })

  it('encodes ids in paths', async () => {
    const api = mockApi({ 'GET /deeds/a%2Fb': { body: DEED } })
    await deedsApi.deed('a/b')
    expect(urlOf(api, 0)).toBe('/api/deeds/a%2Fb')
  })
})

describe('deedsApi: invites', () => {
  it('lists my invites with optional status filter', async () => {
    const page = { items: [INVITE], next_cursor: null }
    const api = mockApi({ 'GET /me/invites': { body: page } })

    await expect(deedsApi.myInvites()).resolves.toEqual(page)
    expect(urlOf(api, 0)).toBe('/api/me/invites')

    await deedsApi.myInvites({ status: 'pending', cursor: 'c1' })
    expect(urlOf(api, 1)).toBe('/api/me/invites?status=pending&cursor=c1')
  })

  it('reissues an invite', async () => {
    const fresh = { ...INVITE, url: 'http://localhost:5173/invite/new-token' }
    const api = mockApi({ 'POST /invites/inv-1/reissue': { body: fresh } })
    await expect(deedsApi.reissueInvite('inv-1')).resolves.toEqual(fresh)
    expect(api.calls('POST /invites/inv-1/reissue')).toHaveLength(1)
  })

  it('previews an invite by token', async () => {
    const preview = {
      status: 'pending',
      reporter: { handle: 'asha', display_name: 'Asha' },
      category: 'food_sharing',
      description: 'Shared lunch',
      occurred_at: '2026-11-14T13:00:00Z',
      expires_at: '2026-11-21T13:00:00Z',
    }
    mockApi({ 'GET /invites/by-token/raw-token': { body: preview } })
    await expect(deedsApi.invitePreview('raw-token')).resolves.toEqual(preview)
  })

  it.each(['invite_expired', 'invite_used', 'invite_cancelled'])(
    'surfaces 410 %s from the preview',
    async (code) => {
      mockApi({ 'GET /invites/by-token/raw-token': { status: 410, body: { detail: 'x', code } } })
      await expect(deedsApi.invitePreview('raw-token')).rejects.toMatchObject({ status: 410, code })
    },
  )

  it('accepts an invite by token', async () => {
    const api = mockApi({ 'POST /invites/by-token/raw-token/accept': { body: DEED } })
    await expect(deedsApi.acceptInvite('raw-token')).resolves.toEqual(DEED)
    expect(api.calls('POST /invites/by-token/raw-token/accept')).toHaveLength(1)
  })
})

describe('deedsApi: self-logs', () => {
  it('creates, lists, edits and deletes self-logs', async () => {
    const api = mockApi({
      'POST /me/self-logs': { status: 201, body: SELF_LOG },
      'GET /me/self-logs': { body: { items: [SELF_LOG], next_cursor: null } },
      'PATCH /me/self-logs/log-1': { body: { ...SELF_LOG, has_location: false } },
      'DELETE /me/self-logs/log-1': { status: 204 },
    })

    const create = {
      category: 'environment',
      description: 'Picked up litter',
      occurred_at: '2026-11-14T08:00:00Z',
      location: LOCATION,
    }
    await expect(deedsApi.createSelfLog(create)).resolves.toEqual(SELF_LOG)
    expect(api.calls('POST /me/self-logs')[0].body).toEqual(create)

    await expect(deedsApi.selfLogs()).resolves.toEqual({ items: [SELF_LOG], next_cursor: null })
    await deedsApi.selfLogs('c9')
    expect(urlOf(api, 1)).toBe('/api/me/self-logs')
    expect(urlOf(api, 2)).toBe('/api/me/self-logs?cursor=c9')

    await deedsApi.updateSelfLog('log-1', { location: null })
    expect(api.calls('PATCH /me/self-logs/log-1')[0].body).toEqual({ location: null })

    await expect(deedsApi.deleteSelfLog('log-1')).resolves.toBeUndefined()
    expect(api.calls('DELETE /me/self-logs/log-1')).toHaveLength(1)
  })
})

describe('deedsApi: profile counts', () => {
  it('fetches public deed counts by handle', async () => {
    const api = mockApi({
      'GET /users/asha/deed-counts': { body: { self_logged: 25, verified: 12 } },
    })
    await expect(deedsApi.deedCounts('asha')).resolves.toEqual({ self_logged: 25, verified: 12 })
    expect(api.calls('GET /users/asha/deed-counts')[0].headers.get('Authorization')).toBeNull()
  })
})

describe('errorDoer', () => {
  const doer = { handle: 'ahmed', display_name: 'Ahmed Khan' }

  it('reads a well-formed doer from an ApiError body', () => {
    expect(errorDoer(new ApiError(409, 'x', 'already_recognized_today', {}, { doer }))).toEqual(
      doer,
    )
  })

  it.each([
    ['no extra', new ApiError(409, 'x', 'already_recognized_today')],
    ['doer not an object', new ApiError(409, 'x', 'c', {}, { doer: 'ahmed' })],
    ['doer missing a name', new ApiError(409, 'x', 'c', {}, { doer: { handle: 'ahmed' } })],
    ['not an ApiError', new Error('offline')],
    ['not an error', null],
  ])('returns null for %s', (_label, error) => {
    expect(errorDoer(error)).toBeNull()
  })

  it('keeps only the public summary fields', () => {
    const error = new ApiError(429, 'x', 'pair_monthly_limit', {}, { doer: { ...doer, id: 'u1' } })
    expect(errorDoer(error)).toEqual(doer)
  })
})

describe('deedsKeys', () => {
  it('keeps every key under deeds', () => {
    const keys = [
      deedsKeys.all,
      deedsKeys.options,
      deedsKeys.scan('s'),
      deedsKeys.deed('d'),
      deedsKeys.myDeeds('doer'),
      deedsKeys.myInvites(),
      deedsKeys.invitePreview('t'),
      deedsKeys.selfLogs,
      deedsKeys.counts('Asha'),
    ]
    for (const key of keys) expect(key[0]).toBe('deeds')
    expect(deedsKeys.options).toEqual(['deeds', 'options'])
    expect(deedsKeys.counts('Asha')).toEqual(['deeds', 'counts', 'asha'])
  })
})
