# M2 Deeds & Verification: design

**Status:** Revision 4 (2026-11-16). Revision 3 was approved and built. Revision 4 adds D15–D18 from the PO's business-logic review (§0.2); the PO approved them for build on 2026-11-16. Frontend screens wait for the PO's Figma designs.
**Baseline:** branch `m2-deeds` (M0 + M1 + architecture docs).
**Sources used:** PRD §4, §5, §6.3, §6.4, §6.6, §8, §9 (same-act), §10.1–10.2, §11, §12; ROADMAP M2; `docs/architecture/README.md` discrepancies #2, #3, #4, #6, #7.

---

## 0. Decisions on known discrepancies

| # | Discrepancy | Decision in this design |
|---|---|---|
| #3 | `/scan/:token` is a placeholder, and `resolve_qr_token` / `resolve_short_id` have no caller | **Resolved.** `POST /api/scans` calls `identity.service.resolve_qr_token` and a new `resolve_manual_code`. `ScanPage` is rewritten. `resolve_short_id` is **removed** from identity's public surface, so no caller can ever treat the static ID alone as verification (M1 review H3). |
| #4 | QR tokens are accepted for about 90 s | **Resolved.** `qr_window_seconds` and `qr_grace_windows` move out of env `Settings` into config rows (namespace `qr`), seeded as `window_seconds=30` and `grace_windows=1`. A token is then accepted for its own window plus one earlier window, so 30–60 s after it is shown. The 6-digit manual code uses the same windows. |
| #6 | Event names | **Resolved.** The deeds module emits `DeedSubmitted`, `DeedVerified` and `DeedRejected` (the union of the PRD and ROADMAP lists). `DeedRejected` carries `new_status: "unverified" \| "rejected"` and a `reason`. Invite expiry is `DeedRejected(new_status="unverified", reason="invite_expired")`. Three extra events: `DeedPrizeRankChanged` (approved, Q9c), and `DeedEdited` and `DeedWithdrawn`, which come from the PO's decision to allow editing and withdrawal (§0.1, D7). |
| #7 | Where self-logs live | **The `deeds` module** (PO approved, Q9a). Self-logs are a simple private record with no link to deeds or scans (§0.1, D3). They sit in `deeds` because the profile shows the self-log count next to the verified-deed count, and both counts come from one endpoint. |
| #2 | Audit log is never written | **M2 writes `audit_log` for:** (a) every config version published; (b) system changes to a deed's prize-relevant fields after creation (`prize_rank_eligible` flip). Deed status transitions are recorded in the new append-only `verification_events` table, which is the PRD's `VerificationEvent` entity. They are not duplicated into `audit_log`. Deed, scan and self-log creation are not audited, because the rows themselves are the record. The identity gaps (QR reset, phone verify, revoke-all) stay out of M2 scope and are listed under §8. |
| (new) | PRD §11 lists *Deed Submission* and *Verification* as separate modules | **Built as one `deeds` module in M2**, with private files per concern. QR verification, same-act grouping and invite acceptance must change deed status, group counters and the verification ledger in one transaction. Two modules would force synchronous cross-module writes, which the boundary rule forbids. The module can be split later along the event seams. Flagged in Q9. |

### 0.1 Product-owner decisions (2026-11-15)

| # | Decision | Where it changes the design |
|---|---|---|
| D1 | **Rate limits use both account ID and IP address** on every limited action. | §2.1 seed, §3.1. The manual-entry per-target counter is also kept (D11). |
| D2 | **Same-act cap is 5**, not 3: reports within 200 m and 2 h form one act, and the first 5 verified reports count. | §2.1 seed `same_act_cap: 5`; §7. PRD §4/§12 to be updated (DOC1). |
| D3 | **Self-logs are simplified.** A "Log a deed" button; category, description, date/time, optional location; private; never earn points, rank or prizes. **No** "Get this verified" button, no conversion, no link to scans. A witness reports the deed separately through the normal QR / ID+code flow. | §2.2, §2.7, §3.2, §5, §7. |
| D4 | **Profile shows two counts**, like followers/following: *Self-log Deeds* and *Verified Deeds*. Both counts are public; self-log details stay owner-only. Self-logs are never included in the verified count or in any scoring. | New endpoint §3.2 #13; §5. Resolves Q2. |
| D5 | **Invite path:** no requirement that the location is the reporter's current position, and no 2-hour limit. | §2.4, §3.2 #3, §3.4. Resolves Q3. |
| D6 | **Shared device between reporter and doer:** allowed, but at most **15 per month** per reporter–doer pair, instead of a hard block. | §2.5, §3.2 #9. Resolves Q4. |
| D7 | **Reporters can edit and withdraw** their deeds, and owners can edit and delete self-logs. | New endpoints §3.2 #14–#15, new table §2.6b, events §4. Resolves Q7. |
| D8 | **Account deletion anonymises** the user's deeds. | §2.8. Resolves Q11. |
| D9 | Existing members can accept invites (Q5); PRD doc updates approved (Q9); QR-verified deeds count for prizes immediately (Q13). Q1, Q6, Q8, Q10 and Q12 take their defaults. | §9. |
| D10 | The phone-history check also runs on the QR path, not only on invite acceptance (recommended in review, accepted with "everything else looks fine"). | §3.2 #1. |
| D11 | **Keep the per-target limit** on typed ID + code (Q14). | §3.1. |
| D12 | ~~Verified Deeds counts reports~~ **Superseded by D15.** | §2.4 index, §3.2 #13, §7. |
| D13 | **Promote the next report** when a counted report is withdrawn (Q16). | §3.4b, §4 (`DeedCountsForPointsChanged`), §7, §8. |
| D14 | Doer-hide is **not in M2**; revisit with moderation in M7 (Q17). | §9. |

### 0.2 Business-logic review (2026-11-16)

The PO walked through the core rules with worked examples. Confirmed unchanged: a doer can never report themselves; self-logs never count; in any 200 m / 2 h window at most 5 reports count, **whatever their category**; separate windows count separately; one report per reporter–doer pair per IST day; no monthly cap on how many deeds a doer gets. The PO's prize metric ("unique person count") is the doer's number of **counted** reports (`counts_for_points = true`, `status = 'verified'`) in the month; the leaderboard itself is M4.

| # | Decision | Where it changes the design |
|---|---|---|
| D15 | **Verified Deeds on the profile counts counted reports**, so it always equals the leaderboard figure: 20 witnesses of one act show 5; 100 reports in separate windows show 100. **Replaces D12** (Q15 reversed). | §3.2 deed-counts, §2.4 index, §7. |
| D16 | **Monthly reporting limit: at most 15 reports per reporter per IST calendar month**, across all doers and both methods. Every `deeds` row the reporter created that month counts, whatever its status now (withdrawn, pending, expired and rejected included), so withdrawing can't free a slot. A failed submission creates no row, so it doesn't count. Self-logs don't count. An invite counts when it is created. Enforced on the server under `advisory_xact_lock("reporter_month", reporter_id)` so concurrent submits can't exceed it. New config key `deeds.reporter_monthly_limit = 15`; error 429 `monthly_report_limit`. | §2.1 seed (v2), §3.2 deed create, §7. |
| D17 | **Invites are switched off** for now. New config key `deeds.invites_enabled = false`. While off, invite create, preview, accept and reissue return 403 `invites_disabled`; the built code and tests stay, and the expiry job keeps running so any existing pending invite still expires. The invite prize rule and naming the doer by @handle are on hold until invites return. | §3.2 invite endpoints, §7. |
| D18 | **Reporters abroad are on hold:** phone numbers stay India-only. | None. |

Still open, to be decided later: QR witness and doer sharing a device (security review M2); the small security fixes L1, L4, L7, L8; the prize tie-break (M4). Invite-specific security finding M1 is on hold with invites.

### Config rows: current state

**No config-row mechanism exists today.** Every limit is an env var in `app/core/config.py` (`Settings`), including `qr_grace_windows`, `qr_window_seconds` and all `rate_*` specs.

M2 creates the mechanism: `app/core/runtime_config.py` plus the `config_versions` table (§2.1). Every M2 limit is read from it. M1's auth/OTP limits move to an `identity` namespace in optional task B10. Secrets, URLs and the cookie flag stay env-only, because they are deployment settings rather than product limits.

---

## 1. Scope

### In (PRD references)

1. **Versioned runtime config rows** (CLAUDE.md, PRD §12), with all M2 limits seeded.
2. **QR hardening from the M1 review:**
   - 60 s grace
   - a rotating 6-digit manual code shown next to the ID; manual entry requires ID + code
   - single-use `(doer, token window, witness)` and `(doer, witness, IST day)` via unique constraints
   - mandatory location and time plausibility checks
   - rate limits per account and per IP on every action, plus a per-target limit on manual entry (Q14)
   - phone-history comparison on both the QR and invite paths
   - device hashes treated as a weak signal
3. **Report by QR / ID+code** (PRD §5a, §6.3): scan, then a deed form, then a deed verified instantly.
4. **Report by invite** (PRD §5b, §10.2):
   - pending deed with no doer and a single-use invite link
   - the deed may have happened up to 7 days ago (config); location is optional and is not required to be the reporter's current position (D5)
   - 5 invites per reporter per IST day
   - 7-day expiry job that sets the deed to `unverified`
   - acceptance: sign up or log in (existing members too), verified phone required, the doer must differ from the reporter (account, all phone-history numbers); a shared device is allowed at most 15 times per month per reporter–doer pair (D6)
   - an invite-verified deed is prize-rank eligible only after a second independent report of the same act (human review comes in M5)
5. **Same-act grouping** (PRD §6.4, §9, §10.1): PostGIS, 200 m / 2 h from the group anchor, **cap 5** (D2), sets `counts_for_points`.
6. **Self-logs** (D3): private records on the user's own profile with create, list, edit and delete. Never linked to deeds, never scored.
7. **Profile deed counts** (D4): public *Self-log Deeds* and *Verified Deeds* counts.
8. **Edit and withdraw** a reported deed by its reporter (D7), with an append-only revision history.
9. **Events** `DeedSubmitted`, `DeedVerified`, `DeedRejected`, `DeedPrizeRankChanged`, `DeedEdited` and `DeedWithdrawn`. Nothing subscribes in M2.
10. **Frontend** (built to the PO's Figma designs):
   - ScanPage rewrite
   - report hub with ID+code entry and the deed form
   - invite form and share screen
   - invite acceptance page
   - manual code on My QR
   - My deeds (with edit and withdraw), my invites and self-logs pages
   - the two counts on own and public profiles

### Out (later milestones)

- Photos/video, feed posts, and the public deed display with coarse area (M3). No coordinates are returned by any M2 API.
- Points, the daily cap of 10, reporter points and scoring (M4). M2 only sets `counts_for_points` and `prize_rank_eligible`.
- Reciprocal, ring, concentration and invite-anomaly detectors, `FlagCase`, human review of invite deeds, and the admin config UI (M5). In M2, config changes go through a data migration.
- Notifications such as "You were recognized" (M6). Moderation removal (M7), apart from the `removed` status value existing.
- A map location picker for invite deeds (until then, an invite deed's location is either the reporter's current position, when they are still at the place, or none).
- Account deletion itself (D8 sets the rule for when it is built).
- Doer-side hiding of a deed reported about them.
- Payout-account comparison (M8).

---

## 2. Data model (database-specialist)

All new deeds tables live in `backend/app/modules/deeds/models.py`. `config_versions` lives in `backend/app/core/runtime_config.py`.

- **Geometry:** `geography(Point, 4326)` via `geoalchemy2`, which is already a dependency.
- **Enums:** `_enum(...)`-style non-native enums with CHECK constraints, as in identity.
- **Naming:** constraint names follow the existing `op.f()` convention, because services map `IntegrityError` to error codes by constraint name.

**Migrations:**

| Revision | Contents |
|---|---|
| `2026_11_13_0000-0004_runtime_config.py` | `config_versions` table, append-only trigger, seed rows |
| `2026_11_13_0100-0005_deeds.py` | All deeds tables |

### 2.1 `config_versions` (core, append-only)

| column | type | notes |
|---|---|---|
| namespace | varchar(40) not null | `qr`, `deeds` (and `identity` in B10) |
| version | integer not null | CHECK `version >= 1` |
| values | jsonb not null | full snapshot of every key in the namespace |
| reason | text not null | |
| created_by | uuid null | no FK (actor may be deleted); null = migration/system |
| created_at | timestamptz not null default now() | |

- **PK:** `(namespace, version)`.
- **Trigger:** `config_versions_append_only` using `forbid_modification()`.
- **Active version:** the highest `version` for the namespace. There is no active flag. A rollback is published as a new version that copies the old values.

**Seed `qr` v1:**
```json
{"window_seconds": 30, "grace_windows": 1}
```

**Seed `deeds` v1** (all keys are required; the Pydantic model has **no defaults**, so the DB is the only source):
```json
{
  "invites_per_day": 5,
  "invite_expiry_hours": 168,
  "same_act_radius_m": 200,
  "same_act_window_minutes": 120,
  "same_act_cap": 5,
  "scan_submit_minutes": 15,
  "qr_occurred_max_age_minutes": 120,
  "invite_occurred_max_age_days": 7,
  "shared_device_pair_monthly_limit": 15,
  "max_location_accuracy_m": 500,
  "max_travel_speed_kmh": 80,
  "travel_slack_m": 1000,
  "service_area": {"lat": 12.9716, "lng": 77.5946, "radius_km": 40},
  "description_min": 10, "description_max": 1000,
  "beneficiaries_max": 10000,
  "categories": [
    {"slug": "helping_elderly", "label": "Helping an elderly person"},
    {"slug": "animals", "label": "Animals"},
    {"slug": "environment", "label": "Environment & cleanliness"},
    {"slug": "food_sharing", "label": "Sharing food"},
    {"slug": "road_safety", "label": "Road & traffic help"},
    {"slug": "medical_help", "label": "Medical / emergency help"},
    {"slug": "community", "label": "Community help"},
    {"slug": "other", "label": "Other"}
  ],
  "rate_scan_witness": "30/3600",
  "rate_scan_ip": "60/3600",
  "rate_manual_witness": "10/900",
  "rate_manual_ip": "20/900",
  "rate_manual_target": "20/900",
  "rate_invite_create_ip": "30/86400",
  "rate_invite_preview_ip": "60/900",
  "rate_invite_accept_user": "10/3600",
  "rate_invite_accept_ip": "30/3600",
  "rate_invite_reissue_user": "10/86400",
  "rate_invite_reissue_ip": "30/86400",
  "rate_deed_edit_user": "30/86400",
  "rate_deed_edit_ip": "100/86400",
  "rate_self_log_user": "20/86400",
  "rate_self_log_ip": "100/86400",
  "rate_profile_counts_ip": "300/900",
  "unconsumed_scan_retention_days": 7
}
```
The category list is the default from Q1. Every limited action has an account counter and an IP counter (D1). Invite creation's account limit is `invites_per_day`; profile counts are public, so they have only an IP counter. The IP values are generous on purpose, because Indian mobile carriers put many users behind one IP; they can be tuned with a new config version.

### 2.2 `qr_scans` (one row per **accepted** scan)

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| witness_user_id | uuid not null FK users RESTRICT | |
| doer_user_id | uuid not null FK users RESTRICT | |
| scan_method | varchar(20) | `qr` \| `manual_code` |
| token_window | bigint not null | the window the token or code matched |
| scanned_at | timestamptz not null | server time |
| scan_day | date not null | IST date of `scanned_at`, set by the app |
| location | geography(Point,4326) not null | witness position |
| accuracy_m | integer not null | CHECK `> 0` |
| device_hash | varchar(64) null | `identity.service.device_hash(X-Device-Id)` |
| ip_hash | varchar(64) null | `keyed_hash("ip", ip)` |
| expires_at | timestamptz not null | `scanned_at + scan_submit_minutes` |
| consumed_at | timestamptz null | set when the deed is created |
| limits_version | integer not null | |

**Constraints:**
- `uq_qr_scans_doer_window_witness UNIQUE (doer_user_id, token_window, witness_user_id)`: ROADMAP single-use `(token window, witness)`.
- `uq_qr_scans_doer_witness_day UNIQUE (doer_user_id, witness_user_id, scan_day)`: ROADMAP `(doer, witness, day)`. An abandoned scan also uses up the pair-day, on purpose.
- CHECK `doer_user_id <> witness_user_id`.

**Indexes:**
- `(witness_user_id, scanned_at DESC)` for witness travel checks
- `(doer_user_id, scanned_at DESC)` for doer travel checks
- `(expires_at) WHERE consumed_at IS NULL`

### 2.3 `same_act_groups`

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| doer_user_id | uuid not null FK users RESTRICT | |
| centre | geography(Point,4326) null | the anchor report's location; null for a deed with no location (an invite deed without one), which then forms a group of one that nothing else can join |
| anchor_at | timestamptz not null | the anchor report's `occurred_at` |
| radius_m | integer not null | copied from config at creation |
| window_minutes | integer not null | copied from config at creation |
| cap | integer not null | copied from config at creation |
| verified_count | integer not null default 0 | PRD `report_count` |
| counted_count | integer not null default 0 | CHECK `counted_count <= cap AND counted_count <= verified_count` |
| distinct_reporters | integer not null default 0 | |
| limits_version | integer not null | |
| created_at | timestamptz | |

**Indexes:** GiST on `centre`; `(doer_user_id, anchor_at)`.

The parameters are copied onto the group so that a later config change never reshuffles existing groups.

### 2.4 `deeds`

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| reporter_user_id | uuid not null FK users RESTRICT | |
| doer_user_id | uuid null FK users RESTRICT | null while an invite is pending |
| category | varchar(40) not null | validated against config at write time |
| description | varchar(1000) not null | |
| beneficiaries_count | integer null | CHECK `BETWEEN 1 AND 10000` (schema ceiling; config may set a lower one) |
| location | geography(Point,4326) null | precise; never returned by the API in M2. Always set for QR deeds (the scan location); optional for invite deeds (D5) |
| location_accuracy_m | integer null | |
| occurred_at | timestamptz not null | |
| reported_at | timestamptz not null default now() | |
| report_day | date not null | IST date of `reported_at`, set by the app |
| status | varchar(20) not null | `pending`, `verified`, `unverified`, `rejected`, `removed`, `withdrawn` |
| withdrawn_at | timestamptz null | set when the reporter withdraws (D7) |
| last_edited_at | timestamptz null | set on every reporter edit; history in `deed_revisions` |
| verification_method | varchar(20) not null | `qr_scan` \| `invite_accept` (the intended path, set at submit) |
| verified_at | timestamptz null | |
| scan_id | uuid null UNIQUE FK qr_scans | |
| same_act_group_id | uuid null FK same_act_groups | |
| counts_for_points | boolean not null default false | |
| prize_rank_eligible | boolean not null default false | |
| limits_version | integer not null | |
| created_at, updated_at | timestamptz | |

**CHECK constraints:**
- `ck_deeds_not_self`: `doer_user_id IS NULL OR doer_user_id <> reporter_user_id` (PRD "never verify own deed", enforced in the DB).
- `ck_deeds_verified_has_doer`: `status <> 'verified' OR (doer_user_id IS NOT NULL AND verified_at IS NOT NULL AND same_act_group_id IS NOT NULL)`.
- `ck_deeds_pending_is_invite`: `status <> 'pending' OR (verification_method = 'invite_accept' AND doer_user_id IS NULL)`.
- `ck_deeds_qr_has_scan`: `verification_method <> 'qr_scan' OR scan_id IS NOT NULL`.
- `ck_deeds_counts_needs_group`: `NOT counts_for_points OR same_act_group_id IS NOT NULL`.
- `ck_deeds_qr_has_location`: `verification_method <> 'qr_scan' OR location IS NOT NULL`.
- `ck_deeds_withdrawn_at`: `(status = 'withdrawn') = (withdrawn_at IS NOT NULL)`.

**Indexes:**
- `uq_deeds_pair_day UNIQUE (doer_user_id, reporter_user_id, report_day) WHERE doer_user_id IS NOT NULL`. This covers the invite path's `(doer, witness, day)` rule.
- GiST `(location)`
- `(doer_user_id, occurred_at)`
- `(reporter_user_id, reported_at DESC)`
- `(same_act_group_id)`
- `(doer_user_id) WHERE status = 'verified'` for the Verified Deeds count

### 2.5 `invites`

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| deed_id | uuid not null UNIQUE FK deeds | |
| reporter_user_id | uuid not null FK users RESTRICT | |
| token_hash | varchar(64) not null UNIQUE | `sha256_hex(raw)`; raw token is never stored |
| sent_via | varchar(20) not null | `whatsapp`, `share_sheet`, `copy_link`, `other` (client-reported) |
| status | varchar(20) not null | `pending`, `accepted`, `expired`, `cancelled` (deed withdrawn while pending) |
| shared_device | boolean not null default false | true when the accepter and reporter share a device hash; counted for the 15/month limit (D6) and kept for M5 fraud checks |
| created_at | timestamptz | |
| created_day | date not null | IST day, used for the 5/day count |
| expires_at | timestamptz not null | |
| reissued_at | timestamptz null | |
| accepted_by | uuid null FK users RESTRICT | |
| accepted_at | timestamptz null | |
| accepted_phone_hash | varchar(64) null | PRD `accepted_phone`, hashed (DPDP) |

**Constraints:**
- CHECK `(status = 'accepted') = (accepted_by IS NOT NULL AND accepted_at IS NOT NULL)`
- CHECK `accepted_by IS NULL OR accepted_by <> reporter_user_id`

**Indexes:** `(reporter_user_id, created_day)`; `(expires_at) WHERE status = 'pending'`; `(reporter_user_id, accepted_by, accepted_at) WHERE shared_device`.

### 2.6 `verification_events` (append-only; trigger `verification_events_append_only`)

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| deed_id | uuid not null FK deeds | |
| kind | varchar(30) not null | `qr_verified`, `invite_accepted`, `invite_expired`, `withdrawn`, `rejected` (M5+) |
| actor_user_id | uuid null | witness, accepting doer, or null for system |
| scan_id | uuid null FK qr_scans | location evidence lives on the scan |
| evidence | jsonb not null | e.g. `{"accuracy_m":35,"witness_speed_kmh":4.1,"doer_speed_kmh":null,"service_area_km":6.2}`; **no raw coordinates** |
| created_at | timestamptz default now() | |

**Index:** `(deed_id)`.

### 2.6b `deed_revisions` (append-only; trigger `deed_revisions_append_only`)

One row per reporter edit (D7), so M4 scoring and M5 fraud checks can see what changed and when.

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| deed_id | uuid not null FK deeds | |
| editor_user_id | uuid not null FK users RESTRICT | always the reporter in M2 |
| old_values | jsonb not null | only the changed fields: `category`, `description`, `beneficiaries_count` |
| new_values | jsonb not null | same keys |
| created_at | timestamptz default now() | |

**Index:** `(deed_id, created_at)`.

### 2.7 `self_logs`

A private record. It has no link to deeds, scans or groups (D3).

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| user_id | uuid not null FK users CASCADE | |
| category | varchar(40) not null | same category list as deeds |
| description | varchar(1000) not null | |
| occurred_at | timestamptz not null | any past date; not more than 5 min in the future, enforced in the app |
| location | geography(Point,4326) null | optional; never returned to anyone but the owner, and never as coordinates to others |
| created_at | timestamptz | |
| updated_at | timestamptz | |

**Index:** `(user_id, created_at DESC)`.

**Edit and delete:** the owner can edit any field or hard-delete the log at any time.

### 2.8 Notes

- **FKs to `users`** use `RESTRICT`: deeds are evidence for other users' points. **Account deletion (D8)** will anonymise rather than delete: the identity user row keeps its id but its name, handle, email, phone and devices are wiped, and it shows as "Deleted user". Deeds by or about the user stay (other users' points and counts depend on them) and point at the anonymised row. The user's self-logs are deleted (`CASCADE`), because they are private. Deletion itself is built later; this rule means M2's schema needs no change for it.
- **IST days:** `report_day`, `scan_day` and `created_day` are set by the app using `identity.service.LAUNCH_TZ`. They are not generated columns, because the timezone conversion is not immutable.

---

## 3. API contract

### 3.1 Conventions

- **Router:** `backend/app/modules/deeds/router.py`, mounted under `/api` by the registry.
- **Errors:** `{detail, code}` via `AppError`. Add `Gone(AppError)` (status 410) to `app/core/errors.py`.
- **Auth:** "Onboarded" means the `identity.service.OnboardedUser` dependency (adult, consents, verified phone, active).
- **Rate limits:** applied **before** any lookup, through `Limiter.hit(key, spec)`, with the spec read from config. Every limited action checks **both** an account key and an IP key (D1); a request is refused if either is over its limit. `ip` is `core.ratelimit.client_ip`.

  | Action | Account key | IP key | Other |
  |---|---|---|---|
  | QR scan | `scan:u:{user_id}` | `scan:ip:{ip}` | |
  | Manual ID + code | `manual:u:{user_id}` | `manual:ip:{ip}` | `manual:t:{short_id}` (Q14) |
  | Create invite | daily count (`invites_per_day`) | `invite:create:ip:{ip}` | |
  | Invite preview (public) | – (no account) | `invite:preview:ip:{ip}` | |
  | Accept invite | `invite:accept:u:{user_id}` | `invite:accept:ip:{ip}` | |
  | Reissue invite | `invite:reissue:u:{user_id}` | `invite:reissue:ip:{ip}` | |
  | Edit / withdraw deed | `deed:edit:u:{user_id}` | `deed:edit:ip:{ip}` | |
  | Self-log create / edit / delete | `selflog:u:{user_id}` | `selflog:ip:{ip}` | |
  | Profile counts (public) | – | `counts:ip:{ip}` | |
- **Testability:** every service function takes `now: datetime | None = None`.

Shared request type:
```
Location = { lat: float (-90..90), lng: float (-180..180), accuracy_m: int (1..100000) }
```

### 3.2 Endpoints

**1. `POST /api/scans`**: Onboarded. Locks in verification proof while the QR or code is fresh.

Request (exactly one identity form):
```json
{ "token": "7K3M9Q0.59071234.ab12…" , "location": {...} }
{ "short_id": "GD-7K3M9Q0", "code": "482913", "location": {...} }
```

`201` response:
```json
{ "scan_id": "uuid", "doer": {"handle": "...", "display_name": "..."}, "expires_at": "..." }
```

Order of checks:
1. Rate limits: account and IP. For manual entry, the manual account, IP and target (`short_id`) limits.
2. Resolve the doer: `resolve_qr_token` or `resolve_manual_code`.
3. Self-check (same account).
4. Phone check (D10): `phone_hashes(witness)` must not intersect `phone_hashes(doer)`.
5. Location checks: accuracy, service area.
6. Witness travel check.
7. Doer travel check: compare with the doer's last accepted scan by anyone.
8. Insert, then map unique violations to error codes.

| Status | code | When |
|---|---|---|
| 401/403 | `not_authenticated`, `onboarding_required` | |
| 404 | `qr_invalid` | Bad, expired or revoked token, or an unknown or inactive doer |
| 404 | `doer_code_invalid` | Manual entry where the ID and code don't match. Identical whether or not the ID exists. |
| 409 | `cannot_verify_self` | Same account, or the two accounts share a phone number |
| 409 | `already_scanned` | `uq_qr_scans_doer_window_witness` |
| 409 | `already_recognized_today` | `uq_qr_scans_doer_witness_day` (also checked before insert) |
| 400 | `location_too_imprecise`, `outside_service_area`, `implausible_travel` | |
| 422 | (validation) | Missing location, or both or neither identity forms |
| 429 | `rate_limited` | |

**2. `GET /api/scans/{scan_id}`**: Onboarded, witness only. Returns the same shape as the `POST`, plus `consumed: bool`, so the deed form survives a page reload.
- 404 `scan_not_found` if the scan isn't the caller's.
- 410 `scan_expired`.

**3. `POST /api/deeds`**: Onboarded. A Pydantic discriminated union on `method`.

QR request:
```json
{ "method": "qr_scan", "scan_id": "uuid", "category": "animals", "description": "...",
  "beneficiaries_count": 1, "occurred_at": "optional; default = scan.scanned_at" }
```
- `occurred_at` must fall within `[scanned_at - qr_occurred_max_age_minutes, scanned_at + 60 s]`.
- The scan is consumed atomically:
  ```sql
  UPDATE qr_scans SET consumed_at=now()
  WHERE id=:id AND witness_user_id=:me AND consumed_at IS NULL AND expires_at > now()
  RETURNING …
  ```
  If no row comes back, inspect the scan to choose 404, 409 or 410.
- The deed's location is the scan location.
- The deed is created `verified`, then grouped (§3.4). The service writes `verification_events(kind=qr_verified)` and emits `DeedSubmitted` and `DeedVerified`. `prize_rank_eligible` is true.

Invite request:
```json
{ "method": "invite", "category": "...", "description": "...", "beneficiaries_count": 2,
  "occurred_at": "...", "location": {...} | null, "sent_via": "whatsapp" }
```
- `occurred_at` must fall within `[now - invite_occurred_max_age_days, now + 60 s]` (D5: no 2-hour limit).
- `location` is **optional** (D5). Without a map picker, the frontend offers "I'm still at the place: add my location"; otherwise it is null. When given, it gets the accuracy and service-area checks, but **no** travel check, because it describes where the deed happened, not where the reporter is moving.
- An invite deed without a location can't be matched to other reports of the same act, so it can never get the second independent report that makes an invite deed prize-rank eligible. It still counts as a verified deed and (from M4) for points. The form says so.
- Daily limit: take `advisory_xact_lock("invites", reporter_id)`, then count `invites WHERE reporter_user_id=:me AND created_day=:today`. All statuses count. If the count is `>= invites_per_day`, return 429 `invite_daily_limit`.
- The service creates a `pending` deed (`doer_user_id` null) and an invite with `expires_at = now + invite_expiry_hours`, then emits `DeedSubmitted(status="pending")`.

`201` response: `DeedOut`. For an invite, it also contains `invite.url = {web_base_url}/invite/{raw_token}`. The raw token is returned **only here and on reissue**.

| Status | code | When |
|---|---|---|
| 404 | `scan_not_found` | |
| 410 | `scan_expired` | |
| 409 | `scan_used` | |
| 409 | `already_recognized_today` | `uq_deeds_pair_day` |
| 400 | `invalid_category`, `implausible_time`, plus the location codes above | |
| 429 | `invite_daily_limit`, `rate_limited` | |

**4. `GET /api/deeds/{id}`**: authenticated; reporter or doer only. Anyone else gets 404 `deed_not_found`. Returns `DeedOut`.

**5. `GET /api/me/deeds?role=doer|reporter&cursor=&limit=20`**: authenticated. Returns `{items: DeedOut[], next_cursor: string|null}`, using keyset pagination on `(reported_at, id)`.

**6. `GET /api/me/invites?status=pending|accepted|expired`**: the reporter's invites, `{items: InviteOut[], next_cursor}`.

**7. `POST /api/invites/{invite_id}/reissue`**: reporter only, pending invites only.
- Rotates `token_hash` and sets `reissued_at`. `expires_at` and the daily count are unchanged.
- Returns `InviteOut` with a fresh `url`.
- Errors: 404, 409 `invite_not_pending`, 429.

**8. `GET /api/invites/by-token/{token}`**: public, no auth, rate-limited per IP.
- Returns `{status: "pending", reporter: {handle, display_name}, category, description, occurred_at, expires_at}`.
- An unknown token gets 404 `invite_invalid`. An expired invite gets 410 `invite_expired`, an accepted one 410 `invite_used`, and a withdrawn one 410 `invite_cancelled`; none of them returns any deed content.
- Responses carry `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

**9. `POST /api/invites/by-token/{token}/accept`**: Onboarded. Rate limit `invite:accept:u`.

Steps:
1. `SELECT … FOR UPDATE` on the invite by `token_hash`.
2. Check the invite: pending and `expires_at > now`.
3. Check accepter ≠ reporter.
4. Check that the two users share no number: `identity.service.phone_hashes(accepter)` must not intersect `phone_hashes(reporter)`. `phone_history` already makes a number belong to only one account ever, so this is defense in depth.
5. Shared-device limit (D6): if `device_hashes(accepter)` intersects `device_hashes(reporter)`, take `advisory_xact_lock("shared_device", reporter_id, accepter_id)` and count this pair's accepted invites with `shared_device = true` in the current IST calendar month. If the count is `>= shared_device_pair_monthly_limit` (15), refuse with 403 `same_device_limit`; otherwise continue and set `shared_device = true` on the invite.
6. Set the deed's doer, `status=verified` and `verified_at`, then group it (§3.4).
7. Set the invite to `accepted`, with `accepted_phone_hash = phone_hash(current phone)`.
8. Write `verification_events(kind=invite_accepted)`.
9. Emit `DeedVerified` with `prize_rank_eligible=false`, then run the prize-rank upgrade (§3.4).

Returns 200 `DeedOut`.

| Status | code |
|---|---|
| 404 | `invite_invalid` |
| 410 | `invite_expired`, `invite_used`, `invite_cancelled` |
| 403 | `cannot_accept_own_invite`, `same_phone`, `same_device_limit` |
| 409 | `already_recognized_today` (pair-day unique) |
| 429 | `rate_limited` |

**10. `GET /api/deeds/options`**: Onboarded.
```json
{ "categories": [...], "invites_remaining_today": 3, "invite_expiry_hours": 168,
  "description_min": 10, "description_max": 1000, "scan_submit_minutes": 15 }
```

**11. Self-logs** (D3; Onboarded, owner only; rate limits `selflog:u` + `selflog:ip` on every write):
- `POST /api/me/self-logs` with `{category, description, occurred_at, location?: Location}`. Returns 201 `SelfLogOut`.
- `GET /api/me/self-logs?cursor=` returns `{items, next_cursor}`.
- `PATCH /api/me/self-logs/{id}` with any of the same fields (`location: null` removes it). Returns 200 `SelfLogOut`.
- `DELETE /api/me/self-logs/{id}` returns 204.
- Anyone else's log gets 404 on every call. No endpoint returns another user's self-log content.
- Self-logs emit **no** events and are never read by grouping, scoring or prize code.

**12. Identity changes:**
- `GET /api/users/me/qr` and `POST /api/users/me/qr/rotate` add `manual_code: "482913"` to `QrCodeResponse`. The code has the same window and expiry as the token.
- `expires_at` now reflects `grace_windows` from config.

**13. `GET /api/users/{handle}/deed-counts`** (D4): public, no auth; rate limit `counts:ip`. Served by the deeds module, which resolves the handle through `identity.service`. Unknown or inactive handle: 404.
```json
{ "self_logged": 25, "verified": 12 }
```
- `self_logged`: the number of the user's self-logs that exist (deleted ones don't count).
- `verified`: the number of **counted** verified reports where the user is the doer, i.e. `COUNT(*)` over the user's deeds with `status = 'verified' AND counts_for_points` (D15). It equals the leaderboard figure: 20 witnesses of one act count as 5. Pending, unverified, rejected, removed and withdrawn deeds don't count. Self-logs are never included.
- Neither number reveals any self-log detail. The own-profile page uses the same endpoint with the user's own handle.

**14. `PATCH /api/deeds/{id}`** (D7): Onboarded, reporter only; rate limits `deed:edit:u` + `deed:edit:ip`.
- Editable fields: `category`, `description`, `beneficiaries_count`. Not editable: `occurred_at`, location, doer, method, status. Those are the verification evidence and the basis for same-act grouping, so changing them would undo the verification.
- Allowed while the deed is `pending` or `verified`. Otherwise 409 `deed_not_editable`.
- In one transaction: update the deed, set `last_edited_at`, insert a `deed_revisions` row with only the changed fields, emit `DeedEdited`. A request that changes nothing returns 200 with no revision and no event.
- Returns 200 `DeedOut`. Non-reporter (including the doer) gets 404.

**15. `POST /api/deeds/{id}/withdraw`** (D7): Onboarded, reporter only; same rate limits as edit.
- Allowed while `pending` or `verified`. Otherwise 409 `deed_not_withdrawable`.
- Pending (invite) deed: the invite becomes `cancelled`, so its link stops working (preview and accept return 410 `invite_cancelled`).
- Verified deed: it leaves its same-act group's counters (§3.4b).
- Sets `status=withdrawn` and `withdrawn_at`, writes `verification_events(kind=withdrawn, actor=reporter)` and emits `DeedWithdrawn`.
- Withdrawal is final. The deed keeps occupying its doer–reporter pair-day, so withdraw-and-resubmit can't be used to retry checks.
- Returns 200 `DeedOut`.

### 3.3 Response shapes

```
DeedOut = {
  id, status, verification_method, category, description, beneficiaries_count,
  occurred_at, reported_at, verified_at, withdrawn_at, last_edited_at,
  reporter: {handle, display_name},
  doer: {handle, display_name} | null,        // always null while pending (PRD §6.5)
  same_act: {report_count, counts_for_points} | null,
  prize_rank_eligible,
  has_location: bool,
  can_edit: bool, can_withdraw: bool,          // true only for the reporter, while pending or verified
  invite: InviteOut | null                     // reporter only
}
InviteOut = { id, deed_id, status, sent_via, created_at, expires_at, accepted_at, url?: string }
SelfLogOut = { id, category, description, occurred_at, has_location: bool, created_at, updated_at }
DeedCountsOut = { self_logged: int, verified: int }
```
No API response contains coordinates in M2.

### 3.4 Same-act grouping (`deeds/_grouping.py`)

Runs when a deed becomes verified (QR submit or invite accept), inside the same transaction.

1. Take `advisory_xact_lock("same_act", doer_id)`.
   - If the deed has **no location** (an invite deed, D5), skip matching: create a group of one with `centre = null` and go to step 4. A null-centre group never matches anything.
2. Find candidate groups (only groups with a non-null `centre` can match):
   ```sql
   SELECT … FROM same_act_groups
   WHERE doer_user_id=:doer
     AND ST_DWithin(centre, :loc, radius_m)
     AND occurred_at BETWEEN anchor_at - window AND anchor_at + window
   ORDER BY abs(extract(epoch from occurred_at - anchor_at)), created_at
   LIMIT 1
   ```
   Matching is against the group anchor, not any member, so groups can't creep along a chain.
3. If no group matches, create one with this deed as the anchor and copy the config parameters onto it.
4. Set `reporter_already_in_group` to whether any verified deed in the group has the same reporter.
5. Set `counts_for_points = counted_count < cap AND NOT reporter_already_in_group`. PRD §4 says each reporter counts at most once per act.
6. Update the counters: `verified_count += 1`, `counted_count += counts_for_points`, and `distinct_reporters += !reporter_already_in_group`.
7. **Prize-rank upgrade:** when `distinct_reporters` becomes ≥ 2, set `prize_rank_eligible=true` on every verified `invite_accept` deed in the group that still has it false. Each flip gets an `audit_log` row (`entity_type="deed"`, `action="prize_rank_eligible.granted"`, `reason="second_independent_report"`, `actor_id=None`) and emits `DeedPrizeRankChanged`.

With `cap = 5` (D2), the first 5 verified reports of an act get `counts_for_points=true`; the 6th and later are saved and linked but don't count unless promoted after a withdrawal (§3.4b).

### 3.4b Withdrawal and same-act groups

When a **verified** deed is withdrawn (§3.2 #15), inside the same `same_act` advisory lock:
1. `verified_count -= 1`; `counted_count -= 1` if the deed had `counts_for_points`; recompute `distinct_reporters` from the group's remaining verified deeds.
2. The withdrawn deed keeps its `counts_for_points` value for history, but it is no longer verified, so it doesn't count.
3. **Promotion (D13):** if the withdrawn deed had `counts_for_points`, the oldest still-verified report in the group with `counts_for_points=false` (ordered by `verified_at`, then `id`), whose reporter has no other counted verified deed in the group, is promoted: set `counts_for_points=true`, `counted_count += 1`, write `audit_log` (`entity_type="deed"`, `action="counts_for_points.granted"`, `reason="promoted_after_withdrawal"`, `actor_id=None`) and emit `DeedCountsForPointsChanged`. If there is no candidate, the slot goes to the next new report. At most one promotion per withdrawal.
4. If `distinct_reporters` drops below 2, any invite deed in the group whose prize-rank eligibility came from the second-report rule loses it: set `prize_rank_eligible=false`, write `audit_log` (`action="prize_rank_eligible.revoked"`, `reason="second_report_withdrawn"`) and emit `DeedPrizeRankChanged`.

When a **pending** deed is withdrawn, its invite becomes `cancelled`. Preview and accept then return 410 `invite_cancelled` with no deed content.

### 3.5 Plausibility (`deeds/_plausibility.py`)

These are pure functions plus two queries. The distance calculation is haversine in Python; PostGIS is used for grouping.

| Check | Rule | Error code |
|---|---|---|
| Location accuracy | `accuracy_m <= max_location_accuracy_m` | `location_too_imprecise` |
| Service area | Distance to the `service_area` centre ≤ `radius_km` | `outside_service_area` |
| Witness travel | QR path only. Against the witness's last accepted scan: `distance - travel_slack_m <= max_travel_speed_kmh × elapsed`. Invite locations describe where a deed happened (possibly days ago), so they are not used here (D5). | `implausible_travel` |
| Doer travel | Same test against the doer's last accepted scan by **any** witness. This catches a screenshot forwarded to a remote witness within the grace window. | `implausible_travel` |
| Time | `occurred_at` within the configured age range (QR: 2 h before the scan; invite: 7 days); judged on the server clock. | `implausible_time` |

Each check result is written into `verification_events.evidence`.

### 3.6 Identity public surface (`identity/service.py` + `_qr.py`)

```python
@dataclass(frozen=True)
class ResolvedDoer: user_id: uuid.UUID; handle: str; display_name: str; window: int; short_id: str

async def resolve_qr_token(session, token: str, now: float | None = None) -> ResolvedDoer
async def resolve_manual_code(session, raw_short_id: str, code: str, now: float | None = None) -> ResolvedDoer
    # checks every window in [now-grace, now] with constant_time_equal; raises NotFound(code="doer_code_invalid")
async def phone_hashes(session, user_id) -> set[str]       # all phone_history rows for the user
def device_hash(device_id: str) -> str                      # keyed_hash("device", id): the single owner of that convention
async def public_summaries(session, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, UserSummary]  # batch handle/display_name
# removed from the public surface: resolve_short_id
```

`_qr` becomes pure and parameterised:
- `current_window(window_seconds, now)`
- `signature_valid(parsed, key_version, window_seconds, grace_windows, now)`
- `window_expiry(window, window_seconds, grace_windows)`
- `manual_code(short_id, key_version, window) -> f"{int(keyed_hash('qr-code', short_id, str(key_version), str(window))[:12], 16) % 1_000_000:06d}"`. The `qr-code` purpose prefix keeps it domain-separated from the token signature.

Identity reads namespace `qr` through `runtime_config.load(session, QrConfig)`. Rotating `key_version` invalidates the codes too.

### 3.7 Core additions

**`app/core/runtime_config.py`:**
- `ConfigVersion` ORM model.
- `class RuntimeConfig(BaseModel)`: `namespace: ClassVar[str]`, `model_config = ConfigDict(extra="forbid", frozen=True)`.
- `async def load[T: RuntimeConfig](session, model: type[T]) -> tuple[int, T]`. Raises `RuntimeError` if no row exists (fails closed). There is no cache in M2: it is one indexed PK lookup per request.
- `async def publish(session, values: RuntimeConfig, *, actor_id, reason) -> int`. Inserts `max+1` and calls `record_audit(entity_type="config", entity_id=namespace, action="config.published", old_value, new_value, reason)`.

**`app/core/db.py`:** `async def advisory_xact_lock(session, *parts: str) -> None`, implemented as `SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))`.

**`app/core/errors.py`:** `class Gone(AppError): status_code = 410`.

**`app/core/config.py`:** remove `qr_window_seconds` and `qr_grace_windows`.

---

## 4. Events (`backend/app/modules/deeds/events.py`)

| Event | `event_type` | Emitted when | Payload | Subscribers |
|---|---|---|---|---|
| `DeedSubmitted` | `deeds.deed_submitted` | Every report about someone else is created (QR or invite). Never for self-logs. | `deed_id, reporter_id, doer_id: UUID\|None, method, status: "pending"\|"verified", category, occurred_at` | None in M2. Later: Feed (M3), Analytics (M8), Notifications (M6). |
| `DeedVerified` | `deeds.deed_verified` | QR submit, invite accept | `deed_id, reporter_id, doer_id, method, verified_at, same_act_group_id, counts_for_points, prize_rank_eligible, limits_version` | None in M2. Later: Scoring (M4), Fraud (M5), Feed (M3), Notifications (M6). |
| `DeedRejected` | `deeds.deed_rejected` | Invite expiry (`new_status="unverified"`, `reason="invite_expired"`). Later: moderator or fraud rejections. | `deed_id, reporter_id, doer_id: UUID\|None, new_status, reason` | None in M2. Later: Feed, Analytics, Scoring reversals. |
| `DeedPrizeRankChanged` | `deeds.prize_rank_changed` | An invite deed gains prize-rank eligibility (§3.4), or loses it because the second report was withdrawn (§3.4b). Later: M5 flags revoke it. | `deed_id, doer_id, prize_rank_eligible, reason` | None in M2. Later: Points (M4). |
| `DeedEdited` | `deeds.deed_edited` | The reporter edits a deed (§3.2 #14) | `deed_id, revision_id, changed_fields: list[str]` (field names only, never the text) | None in M2. Later: Feed (M3) refreshes the post; Scoring (M4) re-scores when `category` changed. |
| `DeedCountsForPointsChanged` | `deeds.counts_for_points_changed` | An uncounted report is promoted after a counted report in its group is withdrawn (§3.4b). | `deed_id, doer_id, reporter_id, same_act_group_id, counts_for_points, reason` | None in M2. Later: Scoring (M4) awards points for the promoted deed. |
| `DeedWithdrawn` | `deeds.deed_withdrawn` | The reporter withdraws a deed (§3.2 #15) | `deed_id, reporter_id, doer_id: UUID\|None, previous_status, same_act_group_id` | None in M2. Later: Feed hides it; Scoring reverses its points with ledger reversal rows. |

Payload and ordering rules:
- **No PII or coordinates in payloads.** Outbox rows are long-lived. Consumers fetch details through `deeds.service`.
- **A QR deed emits `DeedSubmitted` and `DeedVerified` in the same transaction**, so their `occurred_at` values are equal and dispatch order is not guaranteed. `DeedSubmitted.status` therefore carries the current status, and consumers must not depend on ordering.

`deeds/service.py` is the public surface for later modules:
- `get_deed(session, deed_id) -> DeedView | None`
- `deeds_for_doer(...)`
- `same_act_group_view(...)`

---

## 5. UI flows (frontend-developer)

**Visual design comes from the PO's Figma files, which are still to be provided.** This section fixes the screens, states and data; layout and styling follow Figma. Screens without a Figma frame use the existing `ui.tsx` style.

### 5.1 New files

| Area | Files |
|---|---|
| API client | `src/features/deeds/deedsApi.ts` (types + calls mirroring §3) |
| Geolocation | `src/lib/geolocation.ts`: `getPosition(): Promise<Location>` with `enableHighAccuracy: true`, `maximumAge: 0`, `timeout: 15000`. Typed errors: `denied`, `unavailable`, `timeout`. |
| Report | `src/features/report/`: `ReportHubPage`, `ScanPage` (moved from `features/scan`), `ManualCodeForm`, `DeedForm`, `ScanDeedPage`, `InviteDeedPage`, `InviteSharePanel`, `LocationGate` |
| Invite | `src/features/invite/InvitePage.tsx` |
| Deeds | `src/features/deeds/MyDeedsPage.tsx`, `DeedCard.tsx`, `DeedDetailPage.tsx` |
| Self-logs | `src/features/selflog/SelfLogsPage.tsx`, `SelfLogForm.tsx` (create and edit) |
| Profile counts | `src/features/profile/DeedCounts.tsx`, used on ProfilePage and PublicProfilePage |
| Deed edit | `src/features/deeds/EditDeedForm.tsx`, `WithdrawDeedDialog.tsx` |

**No new npm dependency.** In-app scanning uses `BarcodeDetector` when it exists. Otherwise the page tells the user to point the phone camera app at the code, which opens `/scan/:token` directly. Manual ID+code always works.

### 5.2 Routes (`src/app/routes.tsx`)

| Route | Guard | Page |
|---|---|---|
| `/scan/:token` | RequireOnboarded | ScanPage |
| `/report` | RequireOnboarded | ReportHubPage: Scan / Enter ID and code / "They're not on Good Deeds: invite them" |
| `/report/scan/:scanId` | RequireOnboarded | ScanDeedPage (deed form) |
| `/report/invite` | RequireOnboarded | InviteDeedPage |
| `/invite/:token` | public | InvitePage |
| `/me/deeds` | RequireAuth | MyDeedsPage (tabs: Recognised for · I reported · Invites) |
| `/me/deeds/:id` | RequireAuth | DeedDetailPage |
| `/me/self-logs` | RequireOnboarded | SelfLogsPage (list + "Log a deed" form) |

Add "Report a deed" to Layout navigation, a **"Log a deed"** button on ProfilePage, and links to My deeds and Self-logs from ProfilePage.

### 5.3 Flows and states

**ScanPage (`/scan/:token`):**
1. LocationGate explains why location is needed, then calls `getPosition`.
   - **Denied:** explain how to enable location and offer Retry. No verification is possible without location.
   - **Imprecise:** allow a retry.
2. `POST /api/scans`, then `navigate('/report/scan/<scanId>', {replace: true})`. This removes the token from history.
3. Errors:
   - `qr_invalid`: "This code has expired. Ask them to refresh their QR and scan again."
   - `cannot_verify_self`
   - `already_recognized_today`
   - `already_scanned`
   - `implausible_travel` / `outside_service_area` / `location_too_imprecise`: friendly copy for each
   - `rate_limited`
4. **Known limitation:** a witness who isn't signed in goes to login or sign-up with `next`. The QR will have expired by the time they return, so the page says "Ask them to show their code again" (Q6).

**ManualCodeForm (inside ReportHubPage):**
- Fields: ID (`GD-XXXXXXX`, normalised client-side as well) and a 6-digit code (`inputMode="numeric"`).
- Same location gate and errors as ScanPage. `doer_code_invalid` shows "ID and code don't match: codes change every 30 seconds."

**ScanDeedPage / DeedForm:**
- Header: "Recognising **{display_name}** @{handle}", plus a countdown to `expires_at`.
- Fields:
  - category select, from `/api/deeds/options`
  - description with a counter (min/max from options)
  - people helped (optional)
  - "When?", defaulting to now, limited to the last N minutes
- Submit, then a success screen: "Verified ✓", with a same-act note if `counts_for_points` is false ("Others already reported this act; your report is saved as an extra witness").
- Errors: `scan_expired` ("Scan again"), `scan_used`, `already_recognized_today`.
- The form state survives a reload via `GET /api/scans/{id}`.

**InviteDeedPage:**
- Shows `invites_remaining_today` before the form. When it is 0, the form is disabled with an explanation.
- Fields: category, description, people helped, when (up to 7 days ago), and an optional toggle "I'm still at the place: add my location". If it is off, a note explains that the deed can't be matched with other reports of the same act.
- Submit, then InviteSharePanel:
  - WhatsApp button: `https://wa.me/?text=<encoded message + url>`
  - Web Share API button, when `navigator.share` exists
  - Copy link
  - The expiry date
  - A note that the link is shown once and can be reissued from My deeds
- The share text never contains the doer's name.

**InvitePage (`/invite/:token`):**
- Sets `<meta name="referrer" content="no-referrer">` while mounted. The token goes into `sessionStorage['gd_invite']`, and `next=/invite/resume` is used for login, sign-up and onboarding, so the raw token never appears in a URL on pages that load third-party SDKs (Google/Facebook). `/invite/resume` reads it back from sessionStorage.
- States:
  - loading
  - invalid (404)
  - expired (410 `invite_expired`)
  - already used (410 `invite_used`)
  - withdrawn by the reporter (410 `invite_cancelled`)
  - pending: preview card ("{reporter} recognised you for: …")
- Calls to action for a pending invite:
  - logged out: Sign up to accept / Log in
  - not onboarded: Finish setting up
  - onboarded: Accept, then confirmation, then success ("Verified! It's on your profile")
- Errors: `cannot_accept_own_invite`, `same_phone` ("This invite can't be accepted from this account"), `same_device_limit` ("Too many invites between these accounts on a shared device this month"), `already_recognized_today`.

**MyQrPage:**
- Under the ID, show "Code: **482 913**", refreshed with the token. Copy: "They type your ID and this code. It changes every 30 s."

**MyDeedsPage:**
- Three tabs: Recognised for, I reported, Invites.
- Each card shows a status badge (Pending · Verified · Unverified · Rejected · Withdrawn) and "Edited" when `last_edited_at` is set.
- Pending cards never show a doer.
- The Invites tab shows days left and a "Get a new link" button (reissue), which opens the share panel.
- On "I reported" cards with `can_edit` / `can_withdraw`: **Edit** (category, description, people helped) and **Withdraw** (confirmation dialog: "This can't be undone. The deed stops counting for {doer}.").

**SelfLogsPage:**
- A list of the user's self-logs and a **"Log a deed"** form: category, description, date/time (any past date), optional "add my current location".
- Each item has Edit and Delete. There is no verify button and no link to deeds (D3).
- A short note at the top: "Self-logs are private and don't earn points. If someone saw you, ask them to report it by scanning your QR."

**Profile counts (ProfilePage and PublicProfilePage):**
- A row like Instagram's followers/following: **25 Self-log Deeds | 12 Verified Deeds**, from `GET /api/users/{handle}/deed-counts`.
- On the user's own profile, "Self-log Deeds" links to SelfLogsPage and "Verified Deeds" to MyDeedsPage. On someone else's profile the numbers are shown without links to self-log content.

Every page uses `src/components/ui.tsx` primitives, uses TanStack Query keys under `['deeds', …]`, and invalidates `['deeds','options']` after an invite is created.

---

## 6. Task list

Agents: **DB** = database-specialist, **BE** = backend-developer, **FE** = frontend-developer, **QA** = qa-engineer, **SEC** = security-reviewer, **AD** = architecture-diagram.

| ID | Owner | Task | Depends on |
|---|---|---|---|
| D1 | DB | `ConfigVersion` model in `app/core/runtime_config.py`; migration `0004_runtime_config` with the append-only trigger and seed rows `qr` v1 and `deeds` v1 (§2.1) | – |
| D2 | DB | `deeds/models.py` (all §2.2–2.7 tables including `deed_revisions`, constraints, GiST indexes); migration `0005_deeds` with the `verification_events` and `deed_revisions` append-only triggers; constraint tests in `tests/deeds/test_db_constraints.py` | D1 (revision chain) |
| B1 | BE | `runtime_config.load/publish` (with audit), `advisory_xact_lock` in `core/db.py`, `Gone` in `core/errors.py`, `tests/test_runtime_config.py` | D1 |
| B2 | BE | Identity: pure parameterised `_qr`, `manual_code`; `qr` namespace read from config (grace=1); remove the env QR settings; `ResolvedDoer`, `resolve_qr_token` returning the window, `resolve_manual_code`; remove `resolve_short_id`; `phone_hashes`, `device_hash`, `public_summaries`; `QrCodeResponse.manual_code`; update `tests/identity/test_qr.py` | B1 |
| B3 | BE | `deeds` module skeleton: register in `modules/registry.py`, `events.py`, `schemas.py`, `_limits.py` (`DeedLimits(RuntimeConfig)`, `namespace="deeds"`), `_plausibility.py` (pure functions + unit tests) | B1, D2 |
| B4 | BE | `_scans.py` + `POST/GET /api/scans`: account + IP rate limits, both resolvers, phone check, plausibility, unique-violation mapping | B2, B3 |
| B5 | BE | `_grouping.py` + `POST /api/deeds` (QR path): scan consumption, `verification_events`, events; cap 5; null-location groups | B4 |
| B6 | BE | `_invites.py`: invite path of `POST /api/deeds` (7-day `occurred_at`, optional location), daily limit, preview, accept (shared-device 15/month), reissue; prize-rank upgrade with audit; `jobs.py` `deeds.expire_invites` (periodic `*/15 * * * *`, `FOR UPDATE SKIP LOCKED`, batch 200, sets deed `unverified`, `verification_events(invite_expired)`, `DeedRejected`) | B5 |
| B7 | BE | `_self_logs.py`: create, list, edit, delete (no conversion); `GET /api/users/{handle}/deed-counts` | B3 |
| B11 | BE | `_edits.py`: `PATCH /api/deeds/{id}` with `deed_revisions` + `DeedEdited`; `POST /api/deeds/{id}/withdraw` with invite cancel, group counter update and prize-rank revoke (§3.4b) + `DeedWithdrawn` | B5, B6 |
| B8 | BE | Read endpoints: `GET /api/deeds/{id}`, `/api/me/deeds`, `/api/me/invites`, `/api/deeds/options`; `deeds/service.py` public views | B3 (in parallel with B4–B7; finalise after B6) |
| B9 | BE | Nightly `deeds.purge_unconsumed_scans` job (deletes unconsumed scans older than `unconsumed_scan_retention_days`) | D2, B1 |
| B10 | BE | *(Recommended, separable)* Move M1 limits (`rate_*`, `otp_*`) into an `identity` config namespace, seeded by migration `0006` (DB writes the migration) | B1 |
| F1 | FE | `deedsApi.ts` types and calls from §3; `lib/geolocation.ts` + tests | – (contract) |
| F2 | FE | MyQrPage manual code | F1, Figma |
| F3 | FE | ReportHub, ScanPage rewrite, ManualCodeForm, LocationGate, ScanDeedPage/DeedForm | F1, Figma |
| F4 | FE | InviteDeedPage + InviteSharePanel | F1, Figma |
| F5 | FE | InvitePage, `/invite/resume`, sessionStorage handoff | F1, Figma |
| F6 | FE | MyDeedsPage (edit, withdraw), DeedDetailPage, SelfLogsPage, "Log a deed" button, profile counts, nav links | F1, Figma |
| QA1 | QA | Backend rule tests (§7) in `tests/deeds/` | B4–B9, B11 |
| QA2 | QA | Frontend flow tests (§7) | F2–F6 |
| SEC1 | SEC | Review against §8 | QA1, QA2 |
| AD1 | AD | Update diagrams (promote M2 shapes, workflow pages 2 and 3, module map); refresh `docs/architecture/README.md` discrepancies #2/#3/#4/#6/#7 | SEC1 |
| DOC1 | main session | Update PRD (approved, Q9): §11 module table (one `deeds` module, self-logs, events incl. `DeedEdited`/`DeedWithdrawn`, identity's two events); §4/§12 same-act cap 5; §5 invite rules (7-day window, optional location, shared device 15/month, existing members can accept); §5 self-logs (simplified, public counts); §6.2 manual entry needs ID + code; edit/withdraw with promotion of the next report (D13); Verified Deeds counts reports (D12); account deletion anonymises. Update CLAUDE.md (deeds module, `runtime_config`, "limits live in `config_versions`") | PO approval of this revision |

**What can run in parallel:**
- **Start:** D1 and F1 (API client against fetch mocks). F2–F6 start when the Figma designs arrive.
- **After B1:** B2, B10 and D2 run side by side.
- **After B3:** the B4 → B5 → B6 → B11 chain runs alongside B7, B8 and B9.
- **QA:** QA1 can start writing tests from §7 once B3 lands. QA2 can start as soon as the F tasks land.

---

## 7. Test plan (qa-engineer)

### Config
- Seeded `qr` and `deeds` rows validate against their models (all keys present, no extras).
- `load` returns the highest version.
- `publish` increments the version and writes `audit_log`.
- UPDATE or DELETE on `config_versions` raises an error.
- A missing namespace fails closed.
- Changing a limit through `publish` inside a test changes behaviour without a code change. Test at least: invites/day, cap, radius and grace.

### QR and manual code
- A token is accepted in its own window and 1 earlier window, and rejected at 2 earlier windows and for future windows. Grace comes from config.
- The manual code:
  - matches for the current and the previous window
  - is rejected for older windows
  - is wrong after `rotate_qr_key`
  - is never equal to any part of the token signature (domain separation)
- `doer_code_invalid` returns an identical body for an unknown ID and a wrong code.
- `resolve_short_id` no longer exists on `identity.service` (architecture test or import test).
- Revoked doer codes, suspended or non-onboarded doers, and a doer whose `key_version` was rotated all give 404.

### Single-use and anti-self rules
- The same witness scanning the same window twice gets 409 `already_scanned`.
- The same pair on the same IST day (including after a window change, and across UTC midnight before 05:30 IST) gets 409 `already_recognized_today`. The next IST day is allowed.
- Self-scan gets 409. The DB check `ck_deeds_not_self` rejects a direct insert.
- A witness and doer sharing any `phone_history` number get 409 `cannot_verify_self` on the QR path.
- Two concurrent `POST /api/deeds` calls with one `scan_id` create exactly one deed.
- An expired scan gives 410. Another witness's `scan_id` gives 404.

### Plausibility
Boundary tests for each:
- `accuracy_m` at the limit and limit+1
- service-area edge
- witness travel at speed ± ε including slack
- doer travel: two witnesses with the same token 15 km apart 30 s apart → second rejected; 100 m apart → both accepted
- `occurred_at` at the age limit ±1 s (QR 2 h; invite 7 days), and in the future beyond 60 s
- An invite deed with a location far from the reporter's last scan is accepted (no travel check on the invite path)

Also:
- Missing location gives 422.
- Client timestamps are ignored.

### Same-act grouping
- Reports within 200 m and 2 h of the anchor join one group. A report at 201 m or 2 h 1 s starts a new group.
- Chained drift (B is 150 m from A, C is 150 m from B but 300 m from A) does not join C into A's group.
- The first 5 verified reports have `counts_for_points=true`, the 6th and 7th false, and all of them are linked.
- The same reporter twice in one group (across midnight) gives the second report `counts_for_points=false`.
- Two concurrent verifications for a group at 4/5 → exactly one gets the last counted slot (advisory lock).
- An invite deed without a location gets its own null-centre group, and no later report joins it.
- A later config change does not alter existing groups.
- Grouping is per doer: a different doer at the same place gets a separate group.

### Invites
- 5 invites per IST day are allowed; the 6th gets 429 `invite_daily_limit`. Expired or accepted invites still count. The limit resets on the IST day.
- 6 concurrent creates → exactly 5 succeed.
- The token is stored only as a hash. The raw token appears only in the create and reissue responses.
- Reissue invalidates the old token and keeps `expires_at` and the daily count.
- Preview: pending returns content; expired and used return 410 with no content; unknown returns 404; responses are rate-limited and send `no-store`.
- Accept:
  - The reporter accepting gets 403.
  - Shared device: the 15th accepted invite between the same reporter–doer pair in an IST calendar month succeeds (with `shared_device=true`); the 16th gets 403 `same_device_limit`; the next month is allowed again; a different pair on the same device is counted separately; 2 concurrent accepts at 14/15 → exactly one succeeds.
  - An existing onboarded member can accept.
  - A non-onboarded accepter (no phone) gets 403.
  - Accepting twice: the second call gets 410 `invite_used`.
  - Two concurrent accepts by different users → exactly one wins.
  - An accept after `expires_at` but before the job runs gets 410.
  - A pair that already has a deed on the same report day gets 409.
- An accepted deed is `verified`, has its doer set, is grouped, and has `prize_rank_eligible=false`.
- A second report of the same act by a different reporter flips it to true, writes `audit_log` and emits `DeedPrizeRankChanged`. A second report by the **same** reporter does not flip it.
- The expiry job:
  - moves pending to `unverified`, with `verification_events(invite_expired)` and `DeedRejected(new_status="unverified")`
  - is idempotent on re-run
  - skips accepted invites
  - is safe to run concurrently (SKIP LOCKED)
- Pending deeds never expose the doer: `doer` is null in every response, including for the reporter.

### Self-logs and profile counts
- Create, list, edit and delete are owner-only; another user's log gives 404 on every call.
- A self-log with no location, and one dated months ago, are both accepted; one dated more than 5 min in the future is rejected.
- Self-logs emit no events and never touch `deeds`, `same_act_groups` or `qr_scans` (assert row counts unchanged).
- `deed-counts`: `self_logged` counts existing self-logs (drops after a delete); `verified` counts verified reports about the user. 5 reports of one act → 5. Pending, unverified, withdrawn deeds → 0. Self-logs never change `verified`.
- `deed-counts` works without auth, returns no self-log content, 404s for an unknown handle, and is IP rate-limited.

### Edit and withdraw
- Only the reporter can edit or withdraw; the doer and others get 404.
- Edit changes only `category`, `description`, `beneficiaries_count`; attempts to send `occurred_at`, location, status or doer are rejected (422, `extra="forbid"`).
- Each edit writes one `deed_revisions` row with only the changed fields and emits one `DeedEdited`; a no-op edit writes nothing. UPDATE or DELETE on `deed_revisions` raises an error.
- Editing or withdrawing an `unverified`, `rejected` or `withdrawn` deed gives 409.
- Withdrawing a pending deed cancels its invite; preview and accept then give 410 `invite_cancelled`.
- Withdrawing a counted verified deed lowers `verified_count`, then promotes the oldest eligible uncounted report (`counted_count` back to 5, audit row `counts_for_points.granted`, one `DeedCountsForPointsChanged`). With no uncounted report, `counted_count` drops and the next new report takes the slot.
- Promotion skips a report whose reporter already has a counted verified deed in the group, skips withdrawn deeds, and promotes at most one deed per withdrawal. Withdrawing an uncounted report promotes nothing.
- Concurrent withdrawal and new verification in the same group at 5/5 → `counted_count` never exceeds the cap (advisory lock).
- Withdrawing the second independent report revokes the invite deed's prize-rank eligibility (audit row + `DeedPrizeRankChanged`).
- After withdrawal, the same pair can't report again on the same IST day.

### Events and ledgers
- QR submit emits exactly one `DeedSubmitted` (status `verified`) and one `DeedVerified`.
- Invite create emits only `DeedSubmitted` (status `pending`).
- No payload contains coordinates, phone numbers or descriptions.
- UPDATE or DELETE on `verification_events` raises an error.

### Rate limits
Each new limiter key trips at its configured count (InMemoryRateLimiter fixture). For every action, both the account key and the IP key trip independently: one account on many IPs is stopped by the account key, many accounts on one IP by the IP key. The manual per-target limit holds across different witnesses and IPs.

### Frontend
- ScanPage: location-denied state; token replaced in history after a scan; error-code copy.
- ManualCodeForm: normalisation.
- DeedForm: countdown and expiry.
- InvitePage: every state; the token is not in any `next=` URL; sessionStorage resume.
- Invite form disabled at 0 remaining.
- MyQrPage shows and refreshes the manual code.
- Pending cards never render a doer.
- Edit and Withdraw appear only when `can_edit` / `can_withdraw`; withdraw needs confirmation.
- SelfLogsPage: create, edit, delete; no verify button.
- Profile counts render on own and public profiles; links to self-log content only on the own profile.

---

## 8. Security and privacy concerns (security-reviewer)

**Manual-code brute force:**
- The space is 10⁶ × (grace + 1) windows.
- Per-target `manual:t:{short_id}` limiting must hold across witnesses and IPs.
- Rate limits are applied before lookup.
- Identical responses for an unknown ID and a wrong code.
- Verify the constant-time comparison across all candidate windows.

**Screenshot or remote forwarding:**
- The 60 s grace, the per-window uniqueness and the doer travel check reduce this risk; they don't remove it.
- Location comes from the client and can be spoofed. Plausibility is a speed bump, not proof. Confirm the design doesn't claim otherwise, and that M5 detectors are planned to use `qr_scans` (device_hash, ip_hash).

**Invite tokens:**
- 256-bit tokens, stored as SHA-256.
- The token never reaches third-party pages: check the `next=` handoff via sessionStorage, `Referrer-Policy: no-referrer`, and `no-store`.
- The preview leaks deed content to anyone holding the link (acceptable by design). It must not leak the reporter's location or the deed's coordinates.

**Ledger integrity:**
- `config_versions` and `verification_events` have append-only triggers.
- Prize-relevant flips are audited.
- Check that no endpoint can set `counts_for_points`, `prize_rank_eligible`, `status` or `doer_user_id` from client input.

**Race conditions:**
- Grouping cap and invite daily limit (advisory locks).
- Accept (row lock).
- Scan consume (conditional UPDATE).
- Pair-day and window uniqueness (DB constraints, not only pre-checks).

**IDOR:**
- `/api/scans/{id}`, `/api/deeds/{id}`, deed edit and withdraw, invite reissue, and self-log edit and delete must check ownership and return 404 (not 403) to non-owners.

**Edit and withdraw (D7):**
- Edits can't touch verification evidence (`occurred_at`, location, doer, method, status); every edit is in append-only `deed_revisions`.
- Category edits will change M4 points; `DeedEdited` lets scoring re-score with reversal rows. M5 should watch edit frequency.
- Withdrawal can't be used to retry checks (pair-day stays used). With promotion (D13), colluding reporters could withdraw a counted report to let a later one count; the doer's counted total for the act can't rise above the cap, every promotion is audited, and M5 should flag repeated withdraw-then-promote patterns.

**Doer/reporter independence:**
- The phone check uses all `phone_history` rows, on both the QR and the invite path.
- A shared device is allowed up to 15 accepted invites per reporter–doer pair per month (D6). This trades the PRD's hard block for fewer false positives on family phones. The device ID is chosen by the client, so the limit is a speed bump; `invites.shared_device` is kept for M5's invite-anomaly detector.
- An invite can be accepted by a sock-puppet account; only the one-SIM-per-account rule limits that. This is M5 invite-anomaly territory.

**Privacy (DPDP):**
- Precise witness and deed coordinates are stored. Check:
  - none are returned by any M2 API
  - none appear in outbox payloads or `verification_events.evidence`
  - IP is stored only as a keyed hash
  - unconsumed scans are purged
  - the privacy notice covers witness location (Q8)
- Pending deeds never reveal the doer.
- Self-log content (description, category, date, location) is owner-only. Only the two counts are public (D4).
- On account deletion, deeds are anonymised and self-logs deleted (D8).

**Config tampering:**
- Config can only change via migration or `publish`. There is no HTTP endpoint in M2.
- Check `publish` refuses unknown keys (`extra="forbid"`).

**Gaps carried forward (not M2):**
- Identity actions still don't write `audit_log` (discrepancy #2 for identity).
- The architecture test misses relative imports (#5). New deeds code should use absolute imports only.

---

## 9. Open questions for the product owner

### Answered (2026-11-15)

| # | Question | Answer |
|---|---|---|
| Q1 | Deed categories | Default: the 8 seeded categories. |
| Q2 | Self-log visibility | Counts public, details owner-only (D4). |
| Q3 | Invite: current location and 2 h limit? | Not required (D5): up to 7 days ago, optional location. |
| Q4 | Shared device: hard block? | No; at most 15 per month per reporter–doer pair (D6). |
| Q5 | Can existing members accept invites? | Yes. |
| Q6 | Unsigned-up witness re-scans after sign-up | Default: yes. |
| Q7 | Edit / withdraw in M2? | Yes, for reported deeds and self-logs (D7). |
| Q8 | Location retention | Default: life of the deed; unconsumed scans 7 days. |
| Q9 | PRD doc updates | Approved. |
| Q10 | Second independent report | Default: any distinct reporter, QR or invite. |
| Q11 | Account deletion | Anonymise (D8). |
| Q12 | Location mandatory for QR | Default: yes. (Invite location is now optional, D5.) |
| Q13 | QR deeds prize-eligible immediately | Yes. |

### Answered for revision 3 (2026-11-15)

| # | Question | Answer |
|---|---|---|
| Q14 | Per-target limit on typed ID + code? | **Keep** (D11). |
| Q15 | Verified Deeds: count acts or reports? | Count reports (D12), **reversed 2026-11-16: count counted reports (D15).** |
| Q16 | Promote an uncounted report after a counted one is withdrawn? | **Promote** (D13). |
| Q17 | Can the doer hide a deed about them? | **Not in M2**; revisit in M7 (D14). |
