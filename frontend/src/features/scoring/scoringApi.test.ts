import { describe, expect, it } from 'vitest'
import { mockApi } from '../../test/utils'
import { scoringApi, scoringKeys, type Leaderboard, type PublicScoring } from './scoringApi'

const ASHA = { handle: 'asha', display_name: 'Asha' }

const PUBLIC: PublicScoring = {
  tier: { slug: 'seed', label: 'Seed' },
  badges_total: 0,
  titles_total: 0,
  badges: [],
  titles: [],
}

const BOARD: Leaderboard = {
  region: { name: 'Bangalore', kind: 'city' },
  month: '2026-11',
  computed_at: '2026-11-22T10:00:00Z',
  final: false,
  items: [{ rank: 1, shared: false, user: ASHA, tier: PUBLIC.tier, deed_count: 4 }],
  me: null,
  titles: [],
  next_cursor: null,
}

describe('scoringApi (M4 §3.3)', () => {
  it('calls the member endpoints #1–#5 at their paths', async () => {
    const api = mockApi({
      'GET /scoring/options': { body: {} },
      'GET /scoring/me': { body: {} },
      'GET /scoring/me/ledger': { body: { items: [], next_cursor: null } },
      'GET /scoring/users/a%20b': { body: PUBLIC },
      'GET /scoring/deeds/d1': {
        body: { points: 10, reason: 'verified', capped: false, provisional_until: null },
      },
    })
    await scoringApi.options()
    await scoringApi.me()
    await scoringApi.ledger({ role: 'doer', cursor: 'c2' })
    await expect(scoringApi.user('a b')).resolves.toEqual(PUBLIC)
    await scoringApi.deed('d1')
    const ledger = api.calls('GET /scoring/me/ledger')[0]
    expect(ledger.query.get('role')).toBe('doer')
    expect(ledger.query.get('cursor')).toBe('c2')
  })

  it('omits empty leaderboard parameters so the API picks city and the current month', async () => {
    const api = mockApi({ 'GET /leaderboards': { body: BOARD } })
    await expect(scoringApi.leaderboard()).resolves.toEqual(BOARD)
    await scoringApi.leaderboard({ region: 'IN-KA-BLR-W150', month: '2026-10', cursor: 'n' })
    const [first, second] = api.calls('GET /leaderboards')
    expect([...first.query.keys()]).toEqual([])
    expect(second.query.get('region')).toBe('IN-KA-BLR-W150')
    expect(second.query.get('month')).toBe('2026-10')
    expect(second.query.get('cursor')).toBe('n')
  })

  it('searches wards by name (#7) and reads heroes (#8)', async () => {
    const api = mockApi({
      'GET /leaderboards/regions': { body: [{ code: 'W1', name: 'Koramangala' }] },
      'GET /leaderboards/heroes': { body: [] },
    })
    await expect(scoringApi.regions({ q: 'kora' })).resolves.toEqual([
      { code: 'W1', name: 'Koramangala' },
    ])
    await scoringApi.heroes({ region: 'W1', year: 2026 })
    const search = api.calls('GET /leaderboards/regions')[0]
    expect(search.query.get('kind')).toBe('ward')
    expect(search.query.get('q')).toBe('kora')
    const heroes = api.calls('GET /leaderboards/heroes')[0]
    expect(heroes.query.get('region')).toBe('W1')
    expect(heroes.query.get('year')).toBe('2026')
  })

  it('calls the admin endpoints #9–#10', async () => {
    const api = mockApi({
      'GET /admin/scoring/deeds/d1': { body: { ledger: [], evaluations: [] } },
      'GET /admin/scoring/months/2026-11': {
        body: { month: '2026-11', cutoff_at: 'x', pending_scoring_events: 0, finalized_at: null },
      },
    })
    await scoringApi.adminDeed('d1')
    await scoringApi.adminMonth('2026-11')
    expect(api.calls('GET /admin/scoring/deeds/d1')).toHaveLength(1)
    expect(api.calls('GET /admin/scoring/months/2026-11')).toHaveLength(1)
  })

  it('uses the §6.4 query keys', () => {
    expect(scoringKeys.me).toEqual(['scoring', 'me'])
    expect(scoringKeys.leaderboard()).toEqual(['leaderboard', null, null])
    expect(scoringKeys.leaderboard('W1', '2026-11')).toEqual(['leaderboard', 'W1', '2026-11'])
    expect(scoringKeys.user('Asha')).toEqual(['scoring', 'user', 'asha'])
  })
})
