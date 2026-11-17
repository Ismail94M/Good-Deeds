# M2 Deeds & Verification: design

**Status:** Revision 5 (2026-11-17), approved by the PO, built and pushed (m2-deeds, commit 1a9fd6d). Added during the PO review: D28 (same-act grouping by server scan time) and Forgot password (identity, migrations 0011 and 0012; see ROADMAP and PRD §6). Revisions 3 and 4 are approved and built (commit 40a9550). Revision 5 (§0.3, D19–D27) replaces the flat monthly limit with a per-pair limit plus a higher total, and turns the approved Design canvases (F2 My QR, F3 Record a deed, F6 My deeds) into the frontend spec (§5). Decisions marked **needs PO OK** were made by the architect and change something users see.
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
| D16 | ~~15 per reporter per month~~ **Superseded by D19** (the counting rules below still apply to both D19 limits). **Monthly reporting limit: at most 15 reports per reporter per IST calendar month**, across all doers and both methods. Every `deeds` row the reporter created that month counts, whatever its status now (withdrawn, pending, expired and rejected included), so withdrawing can't free a slot. A failed submission creates no row, so it doesn't count. Self-logs don't count. An invite counts when it is created. Enforced on the server under `advisory_xact_lock("reporter_month", reporter_id)` so concurrent submits can't exceed it. New config key `deeds.reporter_monthly_limit = 15`; error 429 `monthly_report_limit`. | §2.1 seed (v2), §3.2 deed create, §7. |
| D17 | **Invites are switched off** for now. New config key `deeds.invites_enabled = false`. While off, invite create, preview, accept and reissue return 403 `invites_disabled`; the built code and tests stay, and the expiry job keeps running so any existing pending invite still expires. The invite prize rule and naming the doer by @handle are on hold until invites return. | §3.2 invite endpoints, §7. |
| D18 | **Reporters abroad are on hold:** phone numbers stay India-only. | None. |

Still open, to be decided later: QR witness and doer sharing a device (security review M2); the small security fixes L1, L4, L8 (L7 decided 2026-11-17, see D28); the prize tie-break (M4). Invite-specific security finding M1 is on hold with invites.

### 0.3 Revision 5: limits and approved screens (2026-11-16/17)

Sources: PO decisions of 2026-11-16/17 (PRD §5, §6.5 and §12 were updated on 2026-11-16), and the approved Design canvases F2, F3 and F6 with their behaviour notes. The F7 Feed canvas is M3 and is not specified here (§5.6 lists what M2 must not block).

| # | Decision | Where it changes the design |
|---|---|---|
| D19 | **Two monthly limits replace D16.** Per IST calendar month a reporter may create at most **15 reports about the same doer** (`deeds.reporter_doer_monthly_limit = 15`, new) and at most **150 reports in total** across all doers (`deeds.reporter_monthly_limit`, raised from 15 to 150). Both count the same way as D16: every `deeds` row the reporter created that month, whatever its status now; failed submissions and self-logs don't count. Both are checked under the existing `reporter_month` advisory lock, so the lock order doesn't change for QR deeds. Errors: 429 `monthly_report_limit` (total, unchanged) and **429 `pair_monthly_limit`** (new). The pair-day rule (one report per pair per IST day) still applies, so reaching 15 for one doer takes 15 different days. | §2.1 v3, §3.2 #1, #3, #9, §7 |
| D20 | **Remaining counts come from the server, with the reset time.** `GET /api/deeds/options` returns `reports_remaining_this_month` (already built), the two limits and `month_resets_at` (00:00 IST on the 1st of next month, as an ISO timestamp). `POST /api/scans` and `GET /api/scans/{id}` return `pair_reports_remaining` and `month_resets_at`. The frontend never computes IST month boundaries itself (a phone may not be on IST). Warnings show only when close: total ≤ 10 left (hub), pair ≤ 3 left (deed form); these thresholds are frontend constants because they only decide when a banner shows. | §3.2 #1, #2, #10, §3.3, §5.3 |
| D21 | **Invite path and the pair limit** (invites are off, D17; this applies when they return). Invite create checks only the total, because the doer is unknown. Acceptance checks the pair limit for the month of the deed's `reported_at` (the month the report already counts in for the total): if the reporter already has `reporter_doer_monthly_limit` deeds about the accepter that month, accept returns 429 `pair_monthly_limit` and the invite stays pending until it expires. To keep the lock order, accept first reads the invite's reporter without a lock, then takes `reporter_month(reporter)`, then the invite row lock. Built now behind the `invites_enabled` flag, so turning invites on can't bypass the pair limit. | §3.2 #9, CLAUDE.md lock order |
| D22 | **Public-feed notice above Send, from M2.** The deed form shows: "Verified deeds appear in the public feed. You or {doer} can hide it at any time. Only the area name is shown, never your exact location." The feed ships in M3, but M2 deeds will be published then (PRD §6.5), so every reporter is told before sending. No backend flag is stored: every deed sent from the M2+ form has shown it, and no deed exists from before launch. | §5.3 F3 |
| D23 | **Photos are hidden in M2.** The canvases design up to 4 photos (Take photo / Upload) on the deed form, the self-log form and the edit form, with a 24 h photo-edit window. Photo storage isn't chosen yet (ROADMAP: before M3), so M2 ships **no photo controls and no photo API**. New config key `deeds.photos_enabled = false`, returned by `GET /api/deeds/options`; the frontend renders photo sections only when it is true, so the photo task (M3) can switch them on without a frontend redeploy. The photo limit (4) and edit window (24 h) become config keys in that task. | §2.1 v3, §3.2 #10, §5 |
| D24 | **The doer sees who recognised them, and only verified deeds.** `DeedOut.reporter` already carries the reporter's public summary (`handle`, `display_name` from `identity.service.public_summaries`, nothing else), so "Priya Sharma recognised you" needs **no** new field. New: the doer's list (`GET /api/me/deeds?role=doer`) returns only `verified` deeds, and `GET /api/deeds/{id}` returns 404 to the doer unless the deed is `verified`. A withdrawn (later: rejected, removed) report disappears from the doer's view instead of showing as "Withdrawn"; the reporter still sees it. **Needs PO OK.** | §3.2 #4, #5 |
| D25 | **Global header.** A blue "Record a deed" button on every page for signed-in users (→ `/report`); nav Home · My QR · Profile; **Log out moves to Profile** (Account section). The reporting section is called "Record a deed" everywhere (was "Report a deed"). | §5.2, §5.3 |
| D26 | **No invite screens in M2.** While invites are off (D17), the frontend builds no invite form, share panel, invite acceptance page or Invites tab (old tasks F4, F5 are deferred). The hub shows a guidance line instead: "Doer not on Good Deeds? Ask them to sign up, then show you their QR." The backend invite code stays as built. | §5, §6 |
| D27 | **Scan-time errors can name the doer.** The canvas copy names the doer ("You've already recognised Ahmed today", "You've recorded Ahmed 15 times this month"), but at scan time the frontend doesn't know who the code belongs to. `AppError` gains an optional `extra` mapping merged into the JSON body; `POST /api/scans` adds `doer: {handle, display_name}` to 409 `already_recognized_today` and 429 `pair_monthly_limit` only. Both are raised only after the code resolved to a valid, current doer and passed the self and phone checks, and in both cases the witness has already recorded that person, so nothing new is revealed. All other errors stay `{detail, code}`. | §3.1, §3.2 #1, §8 |
| D28 | **Same-act grouping uses server time, not the reporter's "When?" (security L7, PO decision L7-A, 2026-11-17).** `occurred_at` can be backdated ("Earlier", up to 2 h on QR, 7 days on invites), so a reporter could pick a time outside an existing group's 2 h window, start a second group for one act, credit the doer twice and get past the cap of 5. Grouping now uses a server-recorded time: a QR deed uses its consumed scan's `scanned_at`; an invite deed uses its `reported_at` (when the reporter created it). The acceptance time is not used, because it can be days after the act. `occurred_at` is stored as entered and used only for display. No new column or migration: both times already exist (`qr_scans.scanned_at`, `deeds.reported_at`). Groups created before this change keep their `occurred_at`-based `anchor_at` (dev data only, pre-launch). Withdrawal and promotion (§3.4b) and `same_act_group_view` don't read either time, so they are unchanged. | §2.3, §3.2 #14, §3.4, §7. |

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
10. **Monthly limits** (D19): 15 per reporter–doer pair and 150 per reporter, with remaining counts for the UI (D20).
11. **Frontend** (built to the approved Design canvases, §5):
   - global header with "Record a deed"; Log out on Profile (D25)
   - My QR with the manual code, countdown bar, large QR and reconnecting state (F2)
   - Record a deed: hub, location gate, in-app camera or camera-app fallback, ID + code, deed form with the public-feed notice, results and every error (F3)
   - My deeds (Recognised for / I reported), deed detail, edit, withdraw, self-logs, profile counts (F6)
   - **Not in M2:** invite screens (D26), photo controls (D23), feed items on the canvases (follows, Saved, "Hide from feed")

### Out (later milestones)

- Photos/video (storage not chosen; D23), feed posts, hide-from-feed, follows, saves, comments and the public deed display with coarse area (M3). No coordinates are returned by any M2 API.
- Invite screens (D26); the invite backend stays built and switched off.
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
| `2026_11_16_0000-0007_deeds_v2.py` | Revision 4: publishes `deeds` v2 (D16, D17) and replaces the Verified Deeds count index (D15) |
| `2026_11_17_0000-0008_deeds_v3.py` | Revision 5: publishes `deeds` v3 (D19, D23). No schema change |

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
  "rate_manual_target_daily": "60/86400",  // v4, migration 0009 (security L4)
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
**`deeds` v2** (migration 0007, revision 4; `created_by` NULL, reason citing D16/D17): v1's values plus
```json
{ "reporter_monthly_limit": 15, "invites_enabled": false }
```
- `reporter_monthly_limit` (D16): deeds a reporter may create per IST calendar month, both paths, all statuses.
- `invites_enabled` (D17): while false, invite create, preview, accept and reissue return 403 `invites_disabled`.

v2 is built from the **latest** version plus the new keys and inserted only if the latest lacks them, so the migration is safe to re-run. Its downgrade is append-only: it publishes a new version equal to the latest minus the two keys (as built in 40a9550; nothing is deleted). `DeedLimits` has both keys with no defaults, so v1 alone no longer validates.

**`deeds` v3** (migration 0008, revision 5; `created_by` NULL, reason citing D19/D23). Same pattern as 0007 (`_publish_from_latest`): the latest values merged with
```json
{ "reporter_doer_monthly_limit": 15, "reporter_monthly_limit": 150, "photos_enabled": false }
```
- `reporter_doer_monthly_limit` (D19): deeds a reporter may create about one doer per IST calendar month, all statuses.
- `reporter_monthly_limit` (D19): raised from 15 to 150; same meaning as in v2.
- `photos_enabled` (D23): false until photo storage ships; only read by `GET /api/deeds/options`.
- Upgrade runs only if the latest version lacks `reporter_doer_monthly_limit`. Downgrade appends a version equal to the latest minus `reporter_doer_monthly_limit` and `photos_enabled`, with `reporter_monthly_limit` set back to 15.
- `DeedLimits` gains `reporter_doer_monthly_limit: int` and `photos_enabled: bool` (no defaults).

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
| anchor_at | timestamptz not null | the anchor report's server grouping time: the scan's `scanned_at` for a QR deed, `reported_at` for an invite deed (D28). Never `occurred_at`. |
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
- `(reporter_user_id, reported_at DESC)`, also used by both monthly limits' counts (D16, D19). The pair count filters `doer_user_id` within the reporter's month (at most 150 rows), so no new index is needed.
- `(same_act_group_id)`
- `ix_deeds_doer_counted (doer_user_id) WHERE status = 'verified' AND counts_for_points` for the Verified Deeds count (D15; replaces `ix_deeds_doer_verified` in migration 0007)

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
- **Errors:** `{detail, code}` via `AppError`. Add `Gone(AppError)` (status 410) to `app/core/errors.py`. Revision 5 (D27): `AppError.__init__` gains `extra: Mapping[str, Any] | None = None`, merged into the body after `detail` and `code` (it may not override them). Only the two scan-time errors in §3.2 #1 use it.
- **Auth:** "Onboarded" means the `identity.service.OnboardedUser` dependency (adult, consents, verified phone, active).
- **Rate limits:** applied **before** any lookup, through `Limiter.hit(key, spec)`, with the spec read from config. Every limited action checks **both** an account key and an IP key (D1); a request is refused if either is over its limit. `ip` is `core.ratelimit.client_ip`.

  | Action | Account key | IP key | Other |
  |---|---|---|---|
  | QR scan | `scan:u:{user_id}` | `scan:ip:{ip}` | |
  | Manual ID + code | `manual:u:{user_id}` | `manual:ip:{ip}` | `manual:t:{short_id}` (Q14), plus `manual:td:{short_id}` at 60 per day (`rate_manual_target_daily`, v4) |
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

`201` response (Revision 5 adds the last three fields, D20):
```json
{ "scan_id": "uuid", "doer": {"handle": "...", "display_name": "..."}, "expires_at": "...",
  "scanned_at": "...", "pair_reports_remaining": 3, "month_resets_at": "2026-12-01T00:00:00+05:30" }
```
- `scanned_at`: the server's scan time. The deed form's "Earlier" choices are offsets from it.
- `pair_reports_remaining`: `max(0, reporter_doer_monthly_limit − deeds this reporter created about this doer this IST month)`, counted before this report, so `1` means "this is the last one this month". Unlocked, informational.
- `month_resets_at`: 00:00 IST on the 1st of the next month (`ist_month(now)[1]`), sent with the `+05:30` offset.

Order of checks:
1. Rate limits: account and IP. For manual entry, the manual account, IP and target (`short_id`) limits.
1b. Total monthly limit pre-check (D16/D19, unlocked): a witness already at `reporter_monthly_limit` gets 429 `monthly_report_limit` before a scan is recorded, because a recorded scan uses up the pair's IST day even if no deed follows. The locked check on deed create is the authoritative one.
2. Resolve the doer: `resolve_qr_token` or `resolve_manual_code`.
3. Self-check (same account).
4. Phone check (D10): `phone_hashes(witness)` must not intersect `phone_hashes(doer)`.
4b. Pair-window and pair-day pre-checks (as built): `already_scanned`, `already_recognized_today` (the latter now with `extra={"doer": {...}}`, D27).
4c. **Pair monthly pre-check (D19, unlocked, new):** if the witness already has `reporter_doer_monthly_limit` deeds about this doer this IST month, 429 `pair_monthly_limit` with `extra={"doer": {...}}`. No scan is recorded, so the pair-day isn't used up. It runs here, after the doer is resolved and the self and phone checks passed, never earlier, so it can't be used to learn anything about a code the witness couldn't already use.
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
| 429 | `monthly_report_limit` | The witness already has `reporter_monthly_limit` (150) deeds this IST month (D16/D19) |
| 429 | `pair_monthly_limit` | The witness already has `reporter_doer_monthly_limit` (15) deeds about this doer this IST month (D19). Body: `{detail, code, doer: {handle, display_name}}` (D27) |

The 409 `already_recognized_today` body also carries `doer: {handle, display_name}` (D27). The `detail` strings never name the doer; the frontend builds the named copy from `doer`.

**2. `GET /api/scans/{scan_id}`**: Onboarded, witness only. Returns the same shape as the `POST`, plus `consumed: bool`, so the deed form survives a page reload. `pair_reports_remaining` and `month_resets_at` are recomputed at read time.
- 404 `scan_not_found` if the scan isn't the caller's.
- 410 `scan_expired`.

**3. `POST /api/deeds`**: Onboarded. A Pydantic discriminated union on `method`.

QR request:
```json
{ "method": "qr_scan", "scan_id": "uuid", "category": "animals", "description": "...",
  "beneficiaries_count": 1, "occurred_at": "optional; default = scan.scanned_at" }
```
- `occurred_at` must fall within `[scanned_at - qr_occurred_max_age_minutes, scanned_at + 60 s]`.
- **Monthly reporting limit (D16), both paths:** before any `deeds` row is created, take `advisory_xact_lock("reporter_month", reporter_id)`, then count the reporter's `deeds` with `reported_at` in the current IST calendar month (boundaries from `identity.service.LAUNCH_TZ`), whatever their status. At or above `reporter_monthly_limit`, return 429 `monthly_report_limit`. On the QR path this runs **before** the scan is consumed (lock order: `reporter_month`, then the scan row, then `same_act`), so a refused report leaves the scan unused; on the invite path it runs before the `invites` lock.
- **Pair monthly limit (D19), QR path:** first read the scan's `doer_user_id` with a plain, witness-scoped `SELECT` (no lock; a scan's doer never changes; a missing or foreign scan gives 404 `scan_not_found` as now). Then, **under the same `reporter_month` lock** and right after the total check, count the reporter's deeds with `doer_user_id = scan.doer` and `reported_at` in the current IST month. At or above `reporter_doer_monthly_limit`, return 429 `pair_monthly_limit` (body `{detail, code}` only; the form already knows the doer). Every deed this reporter creates takes the same lock, so the pair count is serialised too: no new lock, and the lock order is unchanged. A refused report leaves the scan unused. Implement both checks in `_reporter_limit.py` (`ensure_under_monthly_limits(session, reporter_id, doer_id | None, limits, now)`; `doer_id=None` on invite create checks only the total).
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
| 403 | `invites_disabled` | Invite request while `invites_enabled` is false (D17), after the invite IP rate limit |
| 429 | `invite_daily_limit`, `monthly_report_limit`, `rate_limited` | |
| 429 | `pair_monthly_limit` | QR path only (D19) |

**D17, all invite endpoints (#3 invite request, #7, #8, #9):** while `deeds.invites_enabled` is false, each returns 403 `invites_disabled` with a friendly message. The check runs after the endpoint's rate limits and before any lookup, so a real and an unknown token get the same answer. `GET /api/me/invites` (#6) still lists the user's existing invites, and the expiry job keeps running.

**4. `GET /api/deeds/{id}`**: authenticated; reporter or doer only. Anyone else gets 404 `deed_not_found`. Returns `DeedOut`. Revision 5 (D24): the doer gets 404 unless the deed is `verified`.

**5. `GET /api/me/deeds?role=doer|reporter&cursor=&limit=20`**: authenticated. Returns `{items: DeedOut[], next_cursor: string|null}`, using keyset pagination on `(reported_at, id)`. Revision 5 (D24): `role=doer` returns only `status = 'verified'` deeds; `role=reporter` is unchanged (all statuses). Each item's `reporter` is the public summary (`handle`, `display_name`) that the doer's "{name} recognised you" line uses.

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

Steps (Revision 5 adds 0 and 3b, D21):
0. Read the invite's `reporter_user_id` by `token_hash` **without a lock** (it never changes; unknown token → 404 as now), then take `advisory_xact_lock("reporter_month", reporter_id)`. This keeps `reporter_month` first in every lock order.
1. `SELECT … FOR UPDATE` on the invite by `token_hash`.
2. Check the invite: pending and `expires_at > now`.
3. Check accepter ≠ reporter.
3b. **Pair monthly limit (D21):** count the reporter's deeds with `doer_user_id = accepter` and `reported_at` in the IST month of **this deed's** `reported_at`. At or above `reporter_doer_monthly_limit`, return 429 `pair_monthly_limit`; the invite stays pending (it will expire). The pending deed itself has no doer yet, so it isn't in the count.
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
| 429 | `rate_limited`, `pair_monthly_limit` (D21) |

**10. `GET /api/deeds/options`**: Onboarded. As built, plus the Revision 5 fields (D20, D23) that the F3 screens need for copy and warnings:
```json
{ "categories": [...], "invites_enabled": false, "invites_remaining_today": 3, "invite_expiry_hours": 168,
  "reports_remaining_this_month": 10, "reporter_monthly_limit": 150,
  "reporter_doer_monthly_limit": 15, "month_resets_at": "2026-12-01T00:00:00+05:30",
  "description_min": 10, "description_max": 1000, "beneficiaries_max": 10000,
  "scan_submit_minutes": 15, "qr_occurred_max_age_minutes": 120,
  "max_location_accuracy_m": 500, "service_area_radius_km": 40, "same_act_cap": 5,
  "photos_enabled": false }
```
- `reports_remaining_this_month` (built) = `max(0, reporter_monthly_limit − deeds created this IST month)`.
- The limits are returned so the copy ("15 times", "150 deeds", "within 500 m", "within 40 km", "Up to 2 hours") follows config instead of being hard-coded.
- `same_act_cap` (`deeds.same_act_cap`) is the number of reports of one act that count; the extra-witness result note (Q20) uses it.
- Nothing per-doer is returned here; the pair count comes with the scan (#1, #2).

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
- Revision 5 (F2): `QrCodeResponse` adds `window_seconds: int` (from the `qr` namespace), so the countdown bar's full length and the "Changes every 30 s" copy follow config. The bar counts down `refresh_after_seconds` from when the response arrived; the reconnecting state lasts until `expires_at`.

**13. `GET /api/users/{handle}/deed-counts`** (D4): public, no auth; rate limit `counts:ip`. Served by the deeds module, which resolves the handle through `identity.service`. Unknown or inactive handle: 404.
```json
{ "self_logged": 25, "verified": 12 }
```
- `self_logged`: the number of the user's self-logs that exist (deleted ones don't count).
- `verified`: the number of **counted** verified reports where the user is the doer, i.e. `COUNT(*)` over the user's deeds with `status = 'verified' AND counts_for_points` (D15). It equals the leaderboard figure: 20 witnesses of one act count as 5. Pending, unverified, rejected, removed and withdrawn deeds don't count. Self-logs are never included.
- Neither number reveals any self-log detail. The own-profile page uses the same endpoint with the user's own handle.

**14. `PATCH /api/deeds/{id}`** (D7): Onboarded, reporter only; rate limits `deed:edit:u` + `deed:edit:ip`.
- Editable fields: `category`, `description`, `beneficiaries_count`. Not editable: `occurred_at`, location, doer, method, status. Those are the verification evidence, so changing them would undo the verification. (Grouping uses server times, not `occurred_at`; D28.)
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
ScanOut = { scan_id, doer: {handle, display_name}, expires_at, scanned_at,
            pair_reports_remaining: int, month_resets_at }          // Revision 5 (D20)
ScanDetail = ScanOut & { consumed: bool }
```
`UserRef` (`{handle, display_name}`) is the only user information any deeds response carries: never phone, email, avatar URL or ID.
No API response contains coordinates in M2.

### 3.4 Same-act grouping (`deeds/_grouping.py`)

Runs when a deed becomes verified (QR submit or invite accept), inside the same transaction.

**Grouping time `t` (D28, L7-A):** a server-recorded time, never the reporter's `occurred_at` (which can be backdated and is for display only). QR deed: the consumed scan's `scanned_at`. Invite deed: its `reported_at` (when the reporter created it, not the acceptance time).

1. Take `advisory_xact_lock("same_act", doer_id)`.
   - If the deed has **no location** (an invite deed, D5), skip matching: create a group of one with `centre = null` and go to step 4. A null-centre group never matches anything.
2. Find candidate groups (only groups with a non-null `centre` can match):
   ```sql
   SELECT … FROM same_act_groups
   WHERE doer_user_id=:doer
     AND ST_DWithin(centre, :loc, radius_m)
     AND :t BETWEEN anchor_at - window AND anchor_at + window
   ORDER BY abs(extract(epoch from :t - anchor_at)), created_at
   LIMIT 1
   ```
   Matching is against the group anchor, not any member, so groups can't creep along a chain.
3. If no group matches, create one with this deed as the anchor (`anchor_at = t`) and copy the config parameters onto it.
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

**Visual design comes from the approved Design canvases** (PO approval 2026-11-16): F2 My QR, F3 Record a deed, F6 My deeds. Each has an interactive mobile prototype, every state at 390 px and key screens at 1440 px; copy, spacing and colours follow the canvases. The copy quoted below is the canvases' copy; `{doer}` is the doer's `display_name` ("Ahmed Khan" on the canvases, first name "Ahmed" in short sentences), and dates such as "1 December" come from `month_resets_at` formatted in IST. Where the canvases have no screen, this section says so and gives interim copy (listed again in §9 for the PO).

**Where the canvases are:** on 2026-11-17 they exist only in a temporary session folder (`…/scratchpad/f2canvas`, `f3canvas`, `f6canvas`, `f7canvas`, each with `project/Main.dc.html` and `project/canvas.json`). Copy them into the repo (suggested `docs/design/m2/`) before handing out the F tasks, so the frontend developer can open them and they survive a cleanup.

**Not built in M2** although drawn on the canvases: photo sections (D23: render only when `options.photos_enabled`; no photo components in M2), the Invites tab and every invite screen (D26), and everything feed-related: followers / following / Saved line, "Shown in the public feed / Hide from feed" row, and the "it has left the public feed" clause (M3).

### 5.1 Files

| Area | Files |
|---|---|
| API client | `src/features/deeds/deedsApi.ts` (built, F1): add the Revision 5 fields (§3.2 #1, #2, #10, §3.3) and the `ApiError` body field `doer` (D27). `src/features/auth/identityApi.ts`: `QrCode.window_seconds`. |
| Error copy | `src/features/report/errorCopy.ts`: one map from error code to `{title, body, action}` (§5.4), used by every F3 screen. |
| Geolocation | `src/lib/geolocation.ts` (built). Add `permissionState()` via `navigator.permissions.query({name: 'geolocation'})`, returning `'granted' \| 'prompt' \| 'denied' \| 'unknown'`. |
| Camera | `src/features/report/QrCamera.tsx`: `getUserMedia({video: {facingMode: 'environment'}})` + `BarcodeDetector({formats: ['qr_code']})`, polling frames; stops every track on unmount. `src/features/report/parseScanUrl.ts`: accepts only a URL whose origin equals `window.location.origin` and whose path is `/scan/:token`; returns the token or null. |
| Layout | `src/app/Layout.tsx`: header (D25). |
| My QR | `src/features/qr/MyQrPage.tsx` (rewrite), `CountdownBar.tsx`, `LargeQrDialog.tsx` |
| Record a deed | `src/features/report/`: `ReportHubPage`, `LocationGate` (explain / denied / imprecise), `ScanCameraPage`, `ManualCodePage`, `ScanLandingPage` (`/scan/:token`, replaces `features/scan/ScanPage`), `CheckingView`, `ScanDeedPage` + `DeedForm`, `ReportResult`, `ReportErrorView`, `LimitBanner` |
| My deeds | `src/features/deeds/`: `MyDeedsPage`, `DeedCard`, `DeedDetailPage`, `EditDeedPage`, `WithdrawDeedDialog` |
| Self-logs | `src/features/selflog/`: `SelfLogsPage`, `SelfLogFormPage` (create and edit), `DeleteSelfLogDialog` |
| Profile | `src/features/profile/ProfilePage.tsx` (redesign), `PublicProfilePage.tsx` (add counts), `DeedCounts.tsx` |
| Shared form parts | `src/features/deeds/CategoryPills.tsx`, `DescriptionField.tsx` (counter), `PeopleStepper.tsx` (used by the deed form, edit and self-log form) |

**No new npm dependency.** Dialogs are `role="dialog" aria-modal="true"`, close on Esc and return focus. Query keys stay under `['deeds', …]`; invalidate `['deeds','options']` and `['deeds','mine']` after a deed is sent, edited or withdrawn, and the deed-counts query after a self-log is created or deleted.

### 5.2 Routes (`src/app/routes.tsx`)

| Route | Guard | Page |
|---|---|---|
| `/me/qr` | RequireOnboarded | MyQrPage (built; rewritten) |
| `/report` | RequireOnboarded | ReportHubPage |
| `/report/scan` | RequireOnboarded | LocationGate → ScanCameraPage, or the "use your camera app" screen when `BarcodeDetector` or a camera is missing |
| `/report/code` | RequireOnboarded | LocationGate → ManualCodePage |
| `/scan/:token` | RequireOnboarded | ScanLandingPage: LocationGate → Checking → `POST /api/scans` → `navigate('/report/scan/<scanId>', {replace: true})`, so the token leaves history |
| `/report/scan/:scanId` | RequireOnboarded | ScanDeedPage (deed form → result) |
| `/me/deeds` | RequireAuth | MyDeedsPage; `?tab=recognised` (default) or `?tab=reported` |
| `/me/deeds/:id` | RequireAuth | DeedDetailPage |
| `/me/deeds/:id/edit` | RequireOnboarded | EditDeedPage |
| `/me/self-logs` | RequireOnboarded | SelfLogsPage |
| `/me/self-logs/new`, `/me/self-logs/:id/edit` | RequireOnboarded | SelfLogFormPage |

`/report/invite` and `/invite/:token` are **not** added in M2 (D26). A route not in this table gets the existing NotFoundPage.

### 5.3 Screens and states

**Global header (Layout, D25)**
- Signed in: logo "Good Deeds"; nav **Home · My QR · Profile**; a blue **"Record a deed"** button (`LinkButton` to `/report`) on every page. Desktop: nav then button on the right. Mobile: logo and button on the first row, nav full width below.
- **No Log out in the header**; it moves to Profile.
- Signed out: unchanged (Log in, Sign up).

**F2 My QR (`/me/qr`)**
- Title "My QR code", intro "Show this to someone who saw your good deed. They scan it to recognise you."
- QR image, then the **countdown bar, always visible**: "New code in {n} s" and "Changes every {window_seconds} s". The bar is full at a new code and empties to 0; it turns orange for the last 5 s. `n` counts down from `refresh_after_seconds` measured from when the response arrived.
- "Show large QR" opens **LargeQrDialog**: full screen, header "{display_name} · My QR" and a Close button; QR, ID, code and "New code in {n} s", laid out for scanning from about 2 m. Close or Esc returns.
- Section "No camera? Give them your ID and code":
  - "Your ID" with `display_id` and a **Copy ID** button. After copying, the button reads "Copied" and a toast "ID copied" shows for 2.5 s.
  - "Code" with `manual_code` shown as "482 913". **Not copyable**: no copy button, `user-select: none`; it stays readable by screen readers. Help: "They type both. The code changes every 30 s and can't be copied, so read it out or let them look at your screen."
- Only the **current** code is shown. The previous code keeps working for one more window, silently; there is no "previous code" line.
- "How the person who saw your deed records it": 1 "They open Record a deed in Good Deeds and scan your QR." 2 "No camera, or it won't scan? They tap Enter ID and code and type the two above."
- "Worried someone copied your QR?": reset with confirmation, as built ("Resetting makes old screenshots of your QR stop working." → "Resetting makes every earlier copy of your QR code stop working. Your ID stays the same." [Yes, reset my QR] [Cancel] → "Your QR code has been reset. Old screenshots no longer work.").
- States:
  - **Loading** (first load): "Loading your QR code…".
  - **Reconnecting:** a refresh failed but `now < expires_at` of the last good response. Keep the last QR, ID and code visible, **greyed**, with "Reconnecting… You can keep showing this code for now." in place of the countdown. Keep retrying on the normal schedule.
  - **Error:** the first load failed, or `expires_at` has passed with no fresh response: "QR code unavailable" / "We couldn't load your QR code. Check your internet connection and try again." [Try again]. Never show an expired code as usable.
  - **Setup not finished** (fallback when the API says `onboarding_required`; the guard normally redirects first): "Finish setting up to get your QR code. People can record your deeds once you've confirmed you're 18 or over, accepted the terms and verified your phone number." [Finish setting up] → `/onboarding`.

**F3 Record a deed**

Flow: Hub → location (explained the first time) → scan or ID + code → Checking → deed form (send within `scan_submit_minutes`) → result.

*Hub (`/report`)*
- Title "Record a deed", intro "Saw someone do something good? Recognise them while you're both still there."
- Two options: **Scan their QR** ("Fastest. Point your camera at the code on their phone.") and **Enter ID and code** ("If scanning doesn't work, they read out their ID and 6-digit code.").
- Guidance line (no invite option, D26): "Doer not on Good Deeds? Ask them to sign up, then show you their QR."
- "How it works": 1 "Find them: scan their QR or type their ID and code." 2 "We check you're both in the same place." 3 "Say what they did. It's verified straight away."
- **LimitBanner** from `GET /api/deeds/options` (D20):
  - `1 ≤ reports_remaining_this_month ≤ 10`: "{n} deeds left this month. Resets on {date}." (singular "1 deed left").
  - `= 0`: "You've recorded {reporter_monthly_limit} deeds this month, the maximum. You can record more from {date}." Both options are disabled (shown at 50 % opacity, `aria-disabled`).
  - More than 10 left: no banner.

*Location gate (before the camera or the ID + code form)*
- If `permissionState()` is `granted`, skip the explain screen and get a position straight away.
- **Explain first:** "Allow your location" / "We check that you and the person you're recognising are in the same place, so deeds can't be faked from far away." Points: "Your exact location is never shown to anyone." "It's only used to check this report." "Good Deeds works in Bangalore for now." [Allow location], then "Your phone will ask next. Choose **Allow**." The button calls `getPosition()`, which triggers the browser prompt.
- **Denied:** "Location is blocked" / "We can't verify a deed without your location. You can turn it back on in a few taps." Steps: 1 "Tap the icon to the left of the web address (a lock or settings icon)." 2 "Find Location and choose **Allow**." 3 "Come back here and tap **Try again**." Footer "Still not working? Check that Location is switched on in your phone's settings." [Try again]
- **Not precise enough** (`accuracy_m > max_location_accuracy_m`, checked on the client before calling the API, and also on a 400 `location_too_imprecise`): "Your location isn't precise enough" / "Your phone places you somewhere within {accuracy} m. We need it within {max} m." Tips: "Turn on precise location for your browser." "Step outside, or move near a window." "Wait a few seconds, then try again." [Try again]
- **Timeout / unavailable** (no canvas screen): use the not-precise layout with title "We couldn't find your location" and the same tips. *Interim copy.*
- Once a position is held, the next screens show a chip "Location ready · ±{accuracy} m". Before `POST /api/scans`, get a fresh position if the held one is older than 60 s.

*Scan (`/report/scan`)*
- **Camera** (when `'BarcodeDetector' in window` and a rear camera opens): dark full-screen view, [Close], the location chip, "Scan their QR", viewfinder, "Point your camera at the QR on their phone. It scans by itself.", link "Enter ID and code instead". On a detected code, `parseScanUrl`; a foreign QR is ignored with a quiet hint "That's not a Good Deeds QR". A Good Deeds token goes straight to Checking (no navigation to `/scan/:token`, so the token never enters history).
- **Use your camera app** (no `BarcodeDetector`, no camera, or camera permission refused): "Scan with your camera app" / "This browser can't scan inside Good Deeds. Use your phone's own camera instead. It opens Good Deeds and carries on from here." Steps: 1 "Open your phone's Camera app." 2 "Point it at the QR on their phone." 3 "Tap the Good Deeds link that appears." On desktop also: "On a computer? Scanning works best on your phone. Or use their ID and code below." Then "or" and [Enter ID and code instead].
- `/scan/:token` (the camera app's link): location gate, then Checking. A witness who isn't signed in goes through login with `next`; by then the code has usually expired, and the `qr_invalid` screen asks for a new one (Q6).

*Enter ID and code (`/report/code`)*
- "Enter their ID and code" / "Ask them to open My QR in Good Deeds and read out both."
- "Their ID", help "It starts with GD-. Capitals and spaces don't matter." (normalised on the client too).
- "6-digit code" (`inputMode="numeric"`, `autoComplete="off"`), help "It changes every 30 seconds, so type it straight away."
- Location chip, [Continue].
- `doer_code_invalid` shows **inline**: "ID and code don't match. Codes change every 30 seconds. Ask for the code on their screen right now and try again." with the code field outlined red.

*Checking*: "Checking…" / "Matching the code and making sure you're both in the same place." while `POST /api/scans` runs.

*Deed form (`/report/scan/:scanId`)*
- Loads `GET /api/scans/{id}` (survives a reload). `consumed: true` → the `scan_used` screen; 410 → the `scan_expired` screen.
- Header card: initials avatar, "Recognising", **{display_name}**, @handle, a "Matched" chip.
- "Send within {mm:ss}. A scan stays valid for {scan_submit_minutes} minutes." counting down to `expires_at`. At 0 the form is replaced by the `scan_expired` screen.
- **Pair warning** when `1 ≤ pair_reports_remaining ≤ 3`: "You can record {first name} {n} more times this month. Resets on {date}." ("1 more time" when n = 1.)
- "What kind of deed?": category pills from `options.categories` (one selected; default none, required).
- "What did they do?": 3-row textarea, help "At least {description_min} characters. Please leave out phone numbers and addresses.", counter "{len} / {description_max}". Too short on Send: red outline and "Write at least {description_min} characters so people understand what happened."
- (Photos: not rendered in M2, D23.)
- "How many people did they help? (optional)": stepper −/+, starting at 1, minimum 0, maximum `beneficiaries_max`. 0 means "not given" and is sent as `null`.
- "When did it happen?": pills **Just now** (default; `occurred_at` omitted, so the server uses `scanned_at`) and **Earlier**, which reveals "How long before you scanned?" with About 15 minutes / About 30 minutes / About 1 hour / About 2 hours; `occurred_at = scanned_at − that offset`. Options longer than `qr_occurred_max_age_minutes` are not shown. Help "Up to 2 hours before you scanned."
- **Public-feed notice (D22)**, directly above Send: "Verified deeds appear in the public feed. You or {first name} can hide it at any time. Only the area name is shown, never your exact location."
- [Send report], then "It goes on {first name}'s profile as soon as you send it."
- "Cancel" (top) returns to the hub. The scan stays unused but the pair's day is already used (§2.2), so Cancel asks first: "Leave without sending? You can't record {first name} again today." [Leave] [Keep writing]. *Interim copy; not on the canvas.*

*Result*
- `same_act.counts_for_points` true → **"Deed verified"** / "Thanks, {reporter first name}. It now counts on {doer}'s profile."
- false → **"Saved as an extra witness"** / "Others already reported this act. Your report backs theirs up, but it doesn't count a second time on {first name}'s profile."
- Summary: Who {doer} · Deed {category label} · When {occurred_at, e.g. "Today, 4:12 pm"} · "Checked by QR code and location". (No Photos row in M2.)
- [Record another deed] → `/report`; [See my reports] → `/me/deeds?tab=reported`.

*Errors*: full-screen errors use one layout (icon, title, body, primary action, "Back to Home"); see §5.4.

**F6 My deeds, self-logs and profile**

*Profile (`/profile`, own)*
- Avatar initials, display name, "@handle".
- Counts row (D4/D15) from `GET /api/users/{handle}/deed-counts`: **{n} Self-log Deeds | {n} Verified Deeds**. On the own profile "Self-log Deeds" links to `/me/self-logs` and "Verified Deeds" to `/me/deeds` (Recognised for).
- [Log a deed] → `/me/self-logs/new`; link "My QR code".
- "Your activity": **My deeds** ("Reports about you and reports you made") and **Self-logs** ("Your private diary of good deeds").
- "Account": the existing M1 account details (name, handle, phone status, link to the public profile) stay here, then **Log out** (D25).
- Not in M2: the "followers · following · Saved" line, and the "· Bangalore" suffix after the handle (there is no city field; *needs PO OK* to drop it or show a fixed launch city).

*Public profile (`/u/:handle`)*: the counts row without links, and "Verified Deeds were confirmed in person by someone who saw them. Self-logs are private notes: only the number is shown."

*My deeds (`/me/deeds`)*
- Title "My deeds"; two tabs **Recognised for** and **I reported** (the Invites tab returns with invites). Tab intros: "Deeds other people saw you do." / "Deeds you saw others do. You can edit or withdraw your reports."
- Lists from `GET /api/me/deeds?role=doer|reporter`, newest first, with a "Show more" button while `next_cursor` is set (*not on the canvas*).
- Card: status badge (Verified · Withdrawn; Pending/Unverified/Rejected only appear when invites or moderation return), date, " · Edited" when `last_edited_at` is set, the who line, the category label and the description (clamped to 3 lines). Tapping opens the detail.
  - Recognised for: "{reporter display_name} recognised you" (D24). When `same_act.counts_for_points` is false: "Also reported by others. The act counts once on your profile." (*see §9 Q20 on this wording*).
  - I reported: "You recognised {doer display_name}", with [Edit] and [Withdraw] when `can_edit` / `can_withdraw`.
- Empty, Recognised for: "No one has recognised you yet" / "Next time you help someone, show your QR so a witness can record it." [Show my QR]. Empty, I reported (*not on the canvas*): "You haven't recorded a deed yet" / "Saw someone do something good? Record it while you're both there." [Record a deed].

*Deed detail (`/me/deeds/:id`)*
- Badge, an "Edited" chip when edited, title ("You recognised {doer}" or "{reporter} recognised you"), category, description; rows People helped (hidden when null), When, "Checked by QR code and location", Last edited.
- Withdrawn (reporter only): "You withdrew this report on {date}. It no longer counts for {doer}." (the canvas's "and it has left the public feed" clause is added in M3).
- Reporter with `can_edit` / `can_withdraw`: [Edit] [Withdraw]. No "Hide from feed" row in M2.
- **Withdraw dialog:** "Withdraw this report?" / "This can't be undone. The deed stops counting for {doer}." [Withdraw report] [Keep it]. Success shows the detail in its withdrawn state.
- 404 → "This deed isn't available" with a link back to My deeds (*interim copy*).

*Edit (`/me/deeds/:id/edit`)*
- "Edit your report". A locked box "Can't be changed": Who {doer}, When {occurred_at}, Where "Checked by QR and location", note "These are the proof that you were there."
- Category pills, description with counter, people-helped stepper (as on the deed form). Photos not rendered (D23; the 24 h photo window comes with photos).
- "Changes are saved with a history, and the deed shows "Edited"." [Save changes] [Cancel].
- `PATCH` sends only the changed fields; 0 people → `beneficiaries_count: null`. 409 `deed_not_editable` → "This report can't be edited any more." and back to the detail.

*Self-logs (`/me/self-logs`)*
- Title "Self-logs", note "Self-logs are private and don't earn points. If someone saw you, ask them to record it by scanning your QR.", [Log a deed].
- Item: an "Only you" chip, date, category, description, meta ("Location added" when `has_location`), [Edit] [Delete].
- Empty: "No self-logs yet" / "Keep a private note of the good things you do, even when no one is watching."
- **Delete dialog:** "Delete this self-log?" / "It's removed for good. Self-logs don't affect your points." [Delete] [Keep it].

*Log a deed (`/me/self-logs/new`, `/me/self-logs/:id/edit`)*
- "Log a deed" / "Private. Only you can see it, and it doesn't earn points."
- Category pills; "What did you do?" with counter; (photos not rendered, D23).
- "When did it happen?": Date and Time inputs, `max` = now, help "Any date in the past."
- Toggle "Add my current location" / "Optional. Only you can see it." (uses `getPosition()`; denied → the toggle turns off with "Location is blocked in your browser."). In edit, a log with a location shows "Location added" and a [Remove] link that sends `location: null`.
- [Save self-log]. No verify button and no link to deeds (D3).

### 5.4 Error copy (F3; from the canvas's copy sheet)

`{first}` is the doer's first name, `{date}` the reset date. Doer names at scan time come from the error body's `doer` (D27); at the form stage from the scan.

| Code | Where | Title | Body | Action |
|---|---|---|---|---|
| `qr_invalid` | Full screen | This code has expired | Codes change every 30 seconds. Ask them to open My QR again, then scan the new code. | Scan again |
| `doer_code_invalid` | Inline, ID + code | ID and code don't match | Codes change every 30 seconds. Ask for the code on their screen right now and try again. | Continue (retry) |
| `cannot_verify_self` | Full screen | You can't record your own deed | This QR belongs to your account, or to an account that shares your phone number. Scan the QR of the person who did the deed. | Back to Record a deed |
| `already_recognized_today` | Full screen | You've already recognised {first} today | You can recognise the same person once a day. You can recognise {first} again tomorrow, or record someone else now. | Record someone else |
| `already_scanned` | Full screen | You've already scanned this code | Wait for their next code (it changes every 30 seconds), then scan again. | Scan again |
| `outside_service_area` | Full screen | You're outside Bangalore | Good Deeds is open in Bangalore only for now, within {service_area_radius_km} km of the city centre. | Back to Home |
| `location_too_imprecise` | Location screen | Your location isn't precise enough | Your phone places you somewhere within {accuracy} m. We need it within {max} m. | Try again |
| `implausible_travel` | Full screen | We couldn't confirm your location | Your location jumped further than anyone could travel. Turn off any app that changes your location, wait a minute and try again. | Try again |
| `rate_limited` | Full screen | Too many tries | Please wait a few minutes, then try again. | Back to Home |
| `pair_monthly_limit` | Full screen | You've recorded {first} {reporter_doer_monthly_limit} times this month | That's the most for one person. You can record {first} again from {date}, and you can still record anyone else. | Record someone else |
| `monthly_report_limit` | Hub banner, options disabled; full screen if returned by the API | You've recorded {reporter_monthly_limit} deeds this month | That's the monthly maximum. You can record more from {date}. | Back to Home |
| `scan_expired` | Full screen | Time ran out for this report | Reports must be sent within {scan_submit_minutes} minutes of scanning. Scan {first}'s QR again to start over. | Scan again |
| `scan_used` | Full screen | This report was already sent | You'll find it in My deeds. | See my reports |
| location denied | Location screen | Location is blocked | (see §5.3) | Try again |

Not on the copy sheet (*interim copy, §9 Q19*): `qr_invalid` and `already_scanned` use "them" because the doer is unknown at that point (the canvas says "Ahmed"); `scan_not_found` → "This report link isn't valid" / "Start again from Record a deed." [Record a deed]; `invalid_category`, `invalid_description`, `invalid_beneficiaries`, `implausible_time` → inline on the form using the API's `detail`; network failure or 5xx → "Something went wrong" / "Check your internet connection and try again." [Try again].

### 5.5 Frontend rules

- Every screen works at 390 px and 1440 px as drawn; touch targets ≥ 44 px; pills and steppers are real buttons with `aria-pressed` / labels.
- Never render coordinates. Never render a doer on a pending deed.
- Use `month_resets_at` for every "Resets on …" date; never compute IST month ends on the client.
- Warning thresholds are constants in `src/features/report/limits.ts`: `TOTAL_WARN_AT = 10`, `PAIR_WARN_AT = 3`.

### 5.6 F7 Feed (M3): what M2 must not block

- Home `/` becomes the feed for signed-in users in M3; keep the header's Home link pointing at `/`.
- Visibility ("Hide from feed") will be stored separately from verification (PRD §6.5); M2 adds no visibility column and nothing in M2 should read or assume one.
- The M3 public deed page will have its own public route; `/me/deeds/:id` stays the private owner view.
- The profile header leaves room under the counts row for the followers / following / Saved line.
- Area names will be derived from the stored deed location; M2 already stores it for every QR deed.
- The public-feed notice (D22) ships in M2, so M2 deeds can be published in M3.
- Photo storage must be chosen before M3 (ROADMAP); the photo task then adds the 4-photo limit and the 24 h edit window to config and turns on `photos_enabled`.

---

## 6. Task list

Agents: **DB** = database-specialist, **BE** = backend-developer, **FE** = frontend-developer, **QA** = qa-engineer, **SEC** = security-reviewer, **AD** = architecture-diagram.

**Done (Revisions 3–4):** D1, D2, B1–B11, F1, QA1, AD1, DOC1 and the Revision 4 work (D15–D17), commit 40a9550. Old F4/F5 (invite screens) are deferred with invites (D26).

**Remaining work (Revision 5):**

| ID | Owner | Task | Depends on |
|---|---|---|---|
| D3 | DB | Migration `2026_11_17_0000-0008_deeds_v3.py`: publish `deeds` v3 from the latest version (§2.1), append-only downgrade, same helper pattern as 0007 | – |
| B12 | BE | D19 limits: `DeedLimits` gains `reporter_doer_monthly_limit`, `photos_enabled`; `_reporter_limit.py` `ensure_under_monthly_limits(…, doer_id)` (locked, under `reporter_month`) and the scan-time pair pre-check (§3.2 #1 step 4c); QR deed create reads the scan's doer first (§3.2 #3); error code `pair_monthly_limit`; `AppError.extra` + `doer` on the two scan-time errors (D27); update the existing D16 tests that assume a total of 15 | D3 |
| B13 | BE | D20 read fields: `ScanOut`/`ScanDetail` add `scanned_at`, `pair_reports_remaining`, `month_resets_at`; `DeedOptions` adds the fields in §3.2 #10 | B12 |
| B14 | BE | D21 invite acceptance: unlocked reporter read → `reporter_month` lock → invite row; pair check for the deed's month (§3.2 #9). Tests use `invites_on` | B12 |
| B15 | BE | D24: `role=doer` lists only verified deeds; `GET /api/deeds/{id}` 404 for the doer unless verified; update `tests/deeds/test_reads.py` | – |
| B16 | BE | Identity: `QrCodeResponse.window_seconds` (§3.2 #12); update `tests/identity/test_qr.py` | – |
| F1b | FE | `deedsApi.ts` / `identityApi.ts` types for every Revision 5 field and `pair_monthly_limit`; `errorCopy.ts` (§5.4); `limits.ts`; `geolocation.permissionState()`; shared `CategoryPills`, `DescriptionField`, `PeopleStepper` | – (contract) |
| F8 | FE | Layout header: "Record a deed" button, nav Home · My QR · Profile, Log out removed from the header (D25) | – |
| F2 | FE | My QR rewrite (§5.3 F2): countdown bar, Copy ID only, large QR dialog, reconnecting / error / loading / setup states | F1b (uses `window_seconds` from B16; falls back to 30 until it lands) |
| F3a | FE | Record a deed, finding the doer: hub with limit banners, LocationGate (explain, denied, imprecise, timeout), camera + `parseScanUrl`, camera-app fallback, ID + code form, `/scan/:token` landing, Checking, error screens; delete `features/scan/ScanPage` | F1b, F8 |
| F3b | FE | Record a deed, the form: ScanDeedPage + DeedForm (countdown, pair warning, pills, counter, stepper, When?, public-feed notice, cancel confirm), results (verified / extra witness), form-stage errors | F1b, F3a |
| F6a | FE | My deeds: tabs, cards, pagination, empty states, deed detail, withdraw dialog, edit page | F1b |
| F6b | FE | Self-logs: list, empty state, create/edit form, delete dialog | F1b |
| F6c | FE | Profile redesign (counts row with links, activity links, Account with Log out) and public-profile counts | F1b, F8 |
| QA3 | QA | Backend tests for D19–D24 and D27 (§7 "Monthly limits", "Doer view", "Errors"); confirm the old D16 tests now run against 150 | B12–B16 |
| QA4 | QA | Frontend flow tests (§7 "Frontend") | F2, F3a, F3b, F6a–F6c |
| SEC1 | SEC | Review against §8, including the Revision 5 items | QA3, QA4 |
| AD2 | AD | Update diagrams: v3 config, pair limit in the deed-create and accept flows, accept lock order, new frontend routes; `docs/architecture/README.md` | SEC1 |
| DOC2 | main session | CLAUDE.md: the accept lock order becomes `reporter_month` → invite row → `shared_device` → pending deed → `same_act`; deeds limits mention the pair limit; ROADMAP line already updated | PO OK of Revision 5 |

**What can run in parallel:**
- **Start together:** D3, B15, B16, F1b and F8. None depends on another.
- **Backend chain:** D3 → B12 → (B13 and B14 side by side). QA3 can write tests from §7 as soon as B12 lands.
- **Frontend:** after F1b, F2, F3a, F6a and F6b run in parallel (separate folders); F3b follows F3a; F6c follows F8. The frontend builds against the §3 contract with fetch mocks, so it doesn't wait for B12–B16.
- **End:** QA4 when the F tasks land; SEC1 after QA3 and QA4; AD2 after SEC1.

---

## 7. Test plan (qa-engineer)

### Config
- Seeded `qr` and `deeds` rows validate against their models (all keys present, no extras).
- `load` returns the highest version.
- `publish` increments the version and writes `audit_log`.
- UPDATE or DELETE on `config_versions` raises an error.
- A missing namespace fails closed.
- Changing a limit through `publish` inside a test changes behaviour without a code change. Test at least: invites/day, cap, radius and grace.
- `deeds` v2 equals v1 plus `reporter_monthly_limit: 15` and `invites_enabled: false`, with `created_by` NULL; `DeedLimits` rejects a snapshot missing either key.
- `deeds` v3 equals v2 with `reporter_monthly_limit: 150`, plus `reporter_doer_monthly_limit: 15` and `photos_enabled: false`, `created_by` NULL. Re-running the upgrade adds nothing; the downgrade appends a version without the two new keys and with `reporter_monthly_limit: 15`, and deletes nothing. `DeedLimits` rejects a snapshot missing either new key.

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
- Reports within 200 m and 2 h of the anchor join one group. A report at 201 m or 2 h 1 s starts a new group. Time is the scan time (D28).
- D28 (L7-A): reporters who scanned within 2 h and 200 m join one group even when they backdate "When?" differently; scans more than 2 h apart form separate groups even with the same "When?"; backdating can't get more than 5 counted reports out of one act; an invite deed groups by its `reported_at`, not its backdated `occurred_at` or the acceptance time.
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
- `deed-counts`: `self_logged` counts existing self-logs (drops after a delete); `verified` counts **counted** verified reports about the user (D15). 6 or 7 reports of one act → 5; 20 reports of one act → 5; reports in separate windows → each counts; the same reporter twice in one act (across midnight) → 1. A promotion after a withdrawal keeps the count consistent with the group's `counted_count`. Pending, unverified, withdrawn deeds → 0. Self-logs never change `verified`.
- The count's partial index is `ix_deeds_doer_counted` (`status = 'verified' AND counts_for_points`).

### Monthly limits (D16 rules, D19 values)

Seed reports directly as committed `deeds` rows where possible; the pair-day unique constraint means one pair can only get one real report per IST day, so pair boundaries need rows on different `report_day`s in the same month.

*Total (150):*
- 149 deeds this month → the 150th succeeds; at 150 the 151st gets 429 `monthly_report_limit`.
- Reports to different doers, and QR and invite deeds, share the total; the invite path is refused the same way.
- Withdrawn deeds still count. A failed submission (e.g. an expired scan) creates no row and doesn't count. Self-logs don't count.
- A refused QR report leaves its scan unconsumed. `POST /api/scans` at the total returns 429 `monthly_report_limit` and records no scan.
- The total resets at 00:00 IST on the 1st, not 00:00 UTC (23:59 IST on the last day refused; 00:00 IST on the 1st allowed).
- The value comes from config (publishing 2, 3 and 0 changes behaviour).
- Two concurrent submits at 149/150 → exactly one succeeds; the refused report's scan stays unused.

*Pair (15):*
- With 14 deeds about Ahmed this month, the 15th succeeds; with 15, the 16th gets 429 `pair_monthly_limit` on `POST /api/deeds`, and `POST /api/scans` for Ahmed gets 429 `pair_monthly_limit` and records **no** scan (so the pair-day is not used).
- A pair at 15 does **not** affect other doers: the same reporter can still scan and report Kavya, and another reporter can still report Ahmed.
- Withdrawn deeds about Ahmed still count towards the pair; deeds by other reporters about Ahmed don't.
- Concurrency: two submits by one reporter about Ahmed at 14/15 → exactly one succeeds, the other gets `pair_monthly_limit` and its scan stays unused. The pair-day rule allows only one scan per pair per IST day, so the fixture commits two scans with different `scan_day`s and `expires_at` still in the future. Because the locked count runs before the insert, the loser gets `pair_monthly_limit`, not `already_recognized_today`. (Even without the lock, `uq_deeds_pair_day` would stop a second deed that day; the test checks the lock gives the right code.)
- The pair resets at 00:00 IST on the 1st, like the total.
- Order: a reporter at both limits gets `monthly_report_limit` (total checked first).
- The value comes from config (publishing 1 changes behaviour).
- At scan time, the `pair_monthly_limit` and `already_recognized_today` bodies contain `doer: {handle, display_name}` and nothing else beyond `detail` and `code`; no other error body has `doer`. A wrong ID + code, an expired token, or a self/phone failure never returns `pair_monthly_limit`, even when the witness is at the pair limit with someone.
- Invite acceptance (with `invites_on`): reporter with 15 deeds about the accepter in the deed's month → 429 `pair_monthly_limit`, invite still pending, no deed change; 14 → accept succeeds. A deed created on the 30th and accepted on the 2nd counts in the month of the 30th. Accept still works concurrently with the reporter's own QR submit (no deadlock: run both under a barrier).

*Remaining counts (D20):*
- `GET /api/deeds/options`: `reports_remaining_this_month` is 150 − count (never negative), `month_resets_at` is 00:00 IST on the 1st of the next month, including when called at 23:59 IST on the last day and at 00:00 IST on the 1st; the limit fields equal config.
- `POST /api/scans` and `GET /api/scans/{id}`: `pair_reports_remaining` is 15 − pair count before this report (3 when 12 exist; 1 when 14 exist), recomputed on GET after a deed was sent; `scanned_at` equals the stored scan time.

### Doer view (D24)
- `role=doer` lists verified deeds about the user, including ones with `counts_for_points=false`, and never withdrawn, pending, unverified, rejected or removed ones; `role=reporter` still lists all statuses.
- The doer gets 404 `deed_not_found` for a withdrawn deed about them; the reporter still gets it.
- Every item's `reporter` has exactly `handle` and `display_name`.

### Invites switched off (D17)
- With the seeded config, invite create, preview, accept and reissue all return 403 `invites_disabled`; preview keeps `no-store` / `no-referrer`.
- A real token gets the same 403 as an unknown one (check before lookup); the invite row is unchanged; `GET /api/me/invites` still lists it.
- Rate limits still run first (preview's IP limit trips to 429 while disabled).
- The expiry job still expires pending invites.
- The QR path of `POST /api/deeds` is unaffected.
- Publishing `invites_enabled=True` turns every endpoint back on (create, reissue, preview, accept); publishing false turns them off again.
- The existing invite tests run with invites on (the `invites_on` fixture in `tests/deeds/conftest.py`).
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
- Layout: signed-in header has "Record a deed" (→ `/report`) and Home · My QR · Profile, and no Log out; Log out on Profile signs out.
- MyQrPage: shows and refreshes the manual code; the countdown bar turns orange at ≤ 5 s; Copy ID copies `display_id` and shows "Copied"; the code has no copy control; the large-QR dialog opens and closes with Esc; a failed refresh before `expires_at` shows the greyed reconnecting state with the last code, after `expires_at` the error state; first-load failure shows the error state.
- Hub: no banner at 11 left, "10 deeds left this month. Resets on 1 December." at 10, the maximum banner and disabled options at 0; no invite option; the guidance line is present.
- LocationGate: skips the explain screen when permission is granted; denied and imprecise screens; Try again re-requests.
- Camera: `parseScanUrl` accepts only same-origin `/scan/:token`; without `BarcodeDetector` the camera-app screen shows; tracks are stopped on unmount.
- `/scan/:token`: the token is replaced in history after the scan.
- ID + code: normalisation; `doer_code_invalid` inline.
- DeedForm: countdown and expiry; pair warning at 3, none at 4; "1 more time" wording; Just now omits `occurred_at`, Earlier sends `scanned_at − offset`; 0 people sends `null`; the public-feed notice is shown above Send with the doer's name; no photo controls while `photos_enabled` is false.
- Results: "Deed verified" vs "Saved as an extra witness" from `same_act.counts_for_points`.
- Every error code in §5.4 renders its copy; `pair_monthly_limit` and `already_recognized_today` from the scan use the name in the error body's `doer`.
- My deeds: two tabs only; "{reporter} recognised you" on Recognised for; the extra-witness note; Edit and Withdraw only when `can_edit` / `can_withdraw`; withdraw needs confirmation; empty states.
- Edit sends only changed fields; the locked box shows who, when and where.
- Self-logs: create, edit (including removing the location), delete with confirmation; no verify button.
- Profile counts render on own and public profiles; links only on the own profile.
- No screen renders an invite, a photo control, a follower count or "Hide from feed".

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

**Revision 5 checks:**
- **Monthly limits (D19, D21):** both counts run under `reporter_month`; no path creates a `deeds` row without it (QR create, invite create, invite accept). Check the accept path's new unlocked reporter read can't be used to lock a different reporter than the one in the locked invite row (re-check `invite.reporter_user_id` after `FOR UPDATE`).
- **Error bodies naming the doer (D27):** `doer` appears only on scan-time `already_recognized_today` and `pair_monthly_limit`, only after the code resolved and the self and phone checks passed. Confirm no path returns it for a wrong code, an expired token, an unknown ID or a self-scan, so it can't be used to look up who owns an ID. `AppError.extra` can't override `detail` or `code`.
- **Remaining counts (D20):** `pair_reports_remaining` is only returned on the witness's own scan; nothing returns how often *other* people reported a doer.
- **Doer view (D24):** the doer sees the reporter's `handle` and `display_name` only. Withdrawn deeds are hidden from the doer in list and detail.
- **In-app camera:** only same-origin `/scan/:token` URLs are acted on; a QR with any other URL is never opened or fetched. Camera tracks stop when the page closes.
- **Code not copyable (F2):** a speed bump against pasting a code to a remote witness, not a control; the doer travel check and grace window remain the real limits.
- **Public-feed notice (D22):** shown before Send on every deed form. The doer gets no notice in M2 (§9 Q21).

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

Note (Revision 5): PRD §6.5 (2026-11-16) now lets the doer or the reporter hide a deed **from the public feed** in M3. That is a visibility switch, not removal, so D14 still holds for M2 and the M7 question is only about anything beyond feed visibility.

### Open for revision 5 (needs PO OK)

| # | Question | Architect's default in this design |
|---|---|---|
| Q18 | Should a withdrawn report disappear from the doer's My deeds (D24)? | **PO decided 2026-11-17: A (yes).** The doer sees only verified deeds about them; the reporter still sees withdrawn ones. D21 (pair limit checked at invite acceptance) also **approved by the PO (A)**. |
| Q19 | Copy for cases the F3 copy sheet doesn't cover (§5.4: `scan_not_found`, form validation, network errors, location timeout, "I reported" empty state, deed not available) and the Cancel confirmation on the deed form ("Leave without sending? You can't record {first} again today."). | **PO decided 2026-11-17: A.** Cancel shows a confirmation: "Cancel this report?" / "You can't record {first} again until tomorrow." with **Keep writing** (default) and **Cancel report**. Other interim copy as written in §5.3 / §5.4. |
| Q20 | The Recognised-for note "Also reported by others. The act counts once on your profile." is not quite right: an act counts up to 5 times (one per reporter, D2/D15); this report is the one that doesn't count. | **PO decided 2026-11-17: A.** Only on non-counted reports: "Extra witness: 5 people already reported this act, so this report doesn't add to your count." (the number is the same-act cap from config). Counted reports show no note. |
| Q21 | The doer is never told in M2 that verified deeds about them will be public from M3. Add a line to My QR (e.g. "Deeds people record about you appear in the public feed. You can hide any of them.") or to the terms? | **PO decided 2026-11-17: A.** Add to My QR (F2) under the QR: "When someone records a deed you did, it appears in the public Good Deeds feed with your name and the area. You can hide any deed from your profile at any time." Add the same sentence to the Privacy Notice. Repeat as a reminder at M3 launch. |
| Q22 | The profile canvas shows "@handle · Bangalore", but profiles have no city. | **PO decided 2026-11-17: A.** Show "@handle" only; add a city/area field when expanding beyond Bangalore. |
| L7 | Security finding L7 (low): same-act grouping uses the backdatable "When?" (`occurred_at`), so a reporter can split one act into two groups (double credit, cap of 5 bypassed). Group by server time instead? | **PO decided 2026-11-17: A.** Group by server time: QR deeds by the scan's `scanned_at`, invite deeds by `reported_at`; `occurred_at` is display-only (D28). |
