# M3 Feed & Social: design

**Status:** Revision 2 (2026-11-19). All PO questions Q23–Q44 are answered (§11), plus follow-ups recorded with them (Top periods, immediate link cut-off on any photo removal, comment reporting in M3 with a notice to the author, self-likes with zero Top weight). Nothing is built yet. Revision 1 (2026-11-18) was the draft for PO review.
**Baseline:** M2 as built (`m2-deeds`, commit 1a9fd6d, plus forgot-password migrations 0011 and 0012).
**Sources used:** PRD §1, §4, §5, §6.5, §8, §11, §12 (limits table), §15; ROADMAP M3 and "Deferred from M1 (do before launch)"; `docs/M2-DESIGN.md` (D22, D23, D24, Q21, Q22, §5.6); approved canvases `docs/design/f7/` (Feed, PO approval 2026-11-17) and `docs/design/m3-photos/` (Photos and photo reports, PO approval 2026-11-18), including their `canvas.json` decision notes; the code under `backend/app` and `frontend/src`.

Decision numbers continue from M2 (D29 onwards) and question numbers continue from M2 (Q23 onwards), so no number means two things.

---

## 0. Decisions

### 0.1 Architecture decisions

| #   | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Why                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Where                   |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| D29 | **Two new modules: `media` and `feed`.** `media` owns uploads, storage keys, the processing pipeline, signed URLs, photo reports and admin photo decisions. `feed` owns the feed projection, visibility (hide/show), likes, comments, saves, shares and follows.                                                                                                                                                                                                                                                                  | Photos are attached to two owners (deeds and self-logs), carry a reusable pipeline that later privacy features plug into (§4.6), and need their own moderation state. Putting them in `deeds` would grow an already large module with storage and image code that has nothing to do with verification. Reports and decisions must change a photo's state in one transaction, so they live with the photo (the same reasoning that kept verification in one module, M2 §0). M7's Moderation module can later take over report intake along the `PhotoModerationDecided` event seam. | §2, §3                  |
| D30 | **Import direction (no cycles):** `feed.service` → `media.service`, `deeds.service`, `identity.service`; `media.service` → `deeds.service`, `identity.service`; `media/router.py` may read `feed.service` (visibility), but `media/service.py` and its private files never import `feed`. `tests/test_architecture.py` gains a rule for this and a forward rule that no future scoring, points or rewards module may import `app.modules.feed` (visibility must never reach scoring).                                             | The module boundary rule allows reading another module's `service.py`; this keeps the graph acyclic so Python imports can't loop.                                                                                                                                                                                                                                                                                                                                                                                                                                                  | §8, task B26            |
| D31 | **Photos are attached with a separate call after the deed is created** (`POST /api/media/deeds/{deed_id}/photos`), not inside `POST /api/deeds`. The same call adds photos later within the 24 h window.                                                                                                                                                                                                                                                                                                                          | `deeds` can't write `media` rows (boundary rule), and an event-driven attach would put photo IDs and blur regions in long-lived outbox rows. Two calls are simple and reuse one endpoint for "add later". If the second call fails, the deed exists without photos and the result screen says so; the reporter adds them from My deeds.                                                                                                                                                                                                                                            | §3.3, §5.3              |
| D32 | **Upload-then-confirm flow with presigned PUT.** Create an upload (declared type and size) → browser PUTs straight to storage with a short-lived SigV4 URL that signs `Content-Type` and `Content-Length` → `complete` checks the object (HEAD + magic bytes) → attach with blur regions → a worker processes it.                                                                                                                                                                                                                 | Files never pass through the API (stateless, cheap), and every layer re-checks what the client claimed (§4.4).                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | §3.3, §4                |
| D33 | **Blur is applied by the server, once, before any public image exists.** The client only sends normalised ellipse regions. The worker applies them to the full-resolution image, then derives every size from the blurred image. **No unblurred copy is kept** (raw upload deleted after processing). Changing a blur means removing and re-adding the photo. PO decision Q31 = A (2026-11-19).                                                                                                                                   | A blur the browser draws can be undone; only server-side pixels can't. Keeping no original removes the risk of leaking the very face someone asked to hide.                                                                                                                                                                                                                                                                                                                                                                                                                        | §4.5                    |
| D34 | **Serving: private bucket + short-lived signed GET URLs**, not a public bucket + CDN. URLs are signed with a signing time floored to a 30-minute step and valid for 60 minutes (config), so the same image keeps the same URL for up to 30 minutes and browsers cache it.                                                                                                                                                                                                                                                         | Hide, withdraw and private self-log photos must stop being reachable (admin, doer and uploader removals cut off issued links at once, §4.7); public objects and CDN caches can't be recalled. At Bangalore launch scale, egress straight from GCS Mumbai is cheap. A CDN can be added later in front of the media URLs without changing clients (§4.7).                                                                                                                                                                                                                            | §4.7                    |
| D35 | **Storage through the S3-compatible API only** (`app/core/storage.py`, boto3). Production: Google Cloud Storage, bucket in `asia-south1`, XML API at `https://storage.googleapis.com` with HMAC keys and SigV4. Development: **SeaweedFS** in docker-compose (`chrislusf/seaweedfs`, pinned version, Apache-2.0). Tests: an in-memory fake. Switching provider is a settings change.                                                                                                                                              | See §4.2 for the GCS gotchas (no multi-object delete, boto3 checksum defaults) and §4.3 for why SeaweedFS over Garage, S3Proxy, Zenko CloudServer and RustFS.                                                                                                                                                                                                                                                                                                                                                                                                                      | §4.2, §4.3              |
| D36 | **Visibility lives in `feed_posts`** (`hidden_by_doer_at`, `hidden_by_reporter_at`), separate from deed status, counts and prize fields, and nothing in scoring may read it (D30 test).                                                                                                                                                                                                                                                                                                                                           | PRD §6.5: "Public visibility is stored and checked separately from verification and prize eligibility. Scoring never reads it."                                                                                                                                                                                                                                                                                                                                                                                                                                                    | §2.3                    |
| D37 | **The feed is a projection kept by events, checked live on read.** Handlers for `DeedVerified`, `DeedWithdrawn` and `DeedRejected` call one idempotent `sync_post(deed_id)` that reads the deed's current status through `deeds.service` and upserts the post. Every feed read also batch-reads the deeds and drops any that are no longer `verified`, so a withdrawal disappears at once, not after the 5 s dispatch.                                                                                                            | Handlers can run out of order or be retried; syncing from the source of truth makes order irrelevant. The live check closes the outbox delay.                                                                                                                                                                                                                                                                                                                                                                                                                                      | §2.3, §3.4, §5 (events) |
| D38 | **Feed content is read live from `deeds.service`**, not copied. `feed_posts` holds only what ordering, filtering and counting need (IDs, publish time, state, hides, counters). Category, description, people helped and area come from a new batch `deeds.service.get_deeds(ids)`. So `DeedEdited` needs no feed handler.                                                                                                                                                                                                        | No second copy of the description to keep in sync or to erase on account deletion.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | §2.3, §3.1              |
| D39 | **Area names come from a reference table of Bangalore areas in the `deeds` module.** The deeds module (which owns coordinates) resolves `area_id` when a deed is created and exposes only `area_name`. No other module ever sees coordinates. Unmatched points show "Bangalore". Source and granularity (PO decision Q28, 2026-11-19): the latest officially published Greater Bengaluru **ward boundaries** with a suitable licence, one familiar simplified name per ward, never finer than a ward, never an external geocoder. | The rule "area name only, never GPS" is enforced where the coordinates live.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | §2.1, §3.1              |
| D40 | **Cursor pagination everywhere** (opaque keyset cursors like `deeds/_cursor.py`), with partial indexes matching each order. Top (week, month and year, Q26) is a periodic snapshot per period, paginated by rank, because its scores move between page loads.                                                                                                                                                                                                                                                                     | No offset scans; no duplicates or skips across pages.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | §2, §3.4                |
| D41 | **All new limits are config rows** in two new namespaces, `media` and `feed`, typed by `media/_limits.py:MediaLimits` and `feed/_limits.py:FeedLimits` (no defaults). `deeds.photos_enabled` moves to `media.photos_enabled`.                                                                                                                                                                                                                                                                                                     | CLAUDE.md rule; the PRD limits table gains the photo, comment and Top values.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §2.5                    |
| D42 | **Photo reports never change a photo, a deed, its verification, counts or points.** Only an admin decision (`keep`, `remove`, `restore`), with a required note, changes a photo, and each is written to an append-only `photo_decisions` table and to `audit_log`.                                                                                                                                                                                                                                                                | PRD §6.5 and the photos canvas.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | §2.2, §3.5              |
| D43 | **Admin review uses the existing `users.role`** (`member`, `moderator`, `admin`) through `identity.service.require_role`. Roles are granted only with a CLI command (no HTTP endpoint in M3), which writes `audit_log`. An admin can't decide a case on a deed they reported, did, or uploaded photos to. **Both `moderator` and `admin` review photo reports** (PO decision Q34, 2026-11-19); anything added to the admin area later is admin-only unless the PO decides otherwise.                                              | Minimal admin, no new auth system; the full admin area is M5.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §3.5                    |
| D44 | **Social actions need `OnboardedUser`; reading the feed needs only `CurrentUser`.** A signed-in user who hasn't finished onboarding sees the feed, and gets "Finish setting up to like, comment and follow" when they try. PO decision Q42 = A (2026-11-19). Exception: **photo reports** stay open to anyone, as for visitors (rate-limited by IP), so a child-safety or privacy report never waits for setup.                                                                                                                   | Comments are for "onboarded members" (PRD §6.5); likes and follows follow the same rule so one prompt covers all.                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | §3.4                    |
| D45 | **Comment reporting and moderator/admin comment removal move into M3** (PO decision Q38, 2026-11-19). Comment reports live in the `feed` module (which owns comments) with the same shape as photo reports: one case per comment, priority, no automatic hiding, a keep/remove decision with a required note, append-only decisions, audit log. The admin area gets a second queue, "Comment reports", built from the photo-report components.                                                                                    | Abuse the filter misses must have a path to removal before M7; reusing the photo-report pattern keeps it small.                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | §2.3, §3.4a, §6.3       |

### 0.2 What the canvases say, and where this design differs

| #   | Canvas item                                                                                                                                                                         | This design                                                                                                                                                                                                       |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | F7 profile shows "@ahmedk · Bangalore"                                                                                                                                              | Shows "@ahmedk" only (M2 Q22, PO decided 2026-11-17).                                                                                                                                                             |
| C2  | F7 notes say unverified deeds never appear; PRD §6.5 still says "Each post shows its status: Verified or Pending" and §10.1 says a submitted deed is "shown in the feed as Pending" | The feed shows **verified deeds only** (canvas). Invites are off, so no pending deeds exist today. Confirm and fix the PRD wording: Q23.                                                                          |
| C3  | Hidden-deed copy "You or Ahmed can show it again at any time"                                                                                                                       | Built as two separate hides, each undone only by the person who set it (PO decision Q24 = A); the copy becomes "You can show it again at any time."                                                               |
| C4  | The photos canvas's deed form uses a shorter public-feed notice ("You or Ahmed can hide it anytime.")                                                                               | Keep the approved M2 D22 sentence unchanged; it already covers this.                                                                                                                                              |
| C5  | Photos canvas result: "Deed recorded … It's now in the public feed with your photos."                                                                                               | Keep the M2 result screens ("Deed verified" / "Saved as an extra witness") and add one line about photos (§6.3). Photos take a few seconds to process, so "with your photos" may not yet be true. _Interim copy._ |
| C6  | Admin review shows "Verified · Shown in feed"                                                                                                                                       | Built: `media/router.py` reads visibility through `feed.service` (D30).                                                                                                                                           |
| C9  | Admin queue chips Child / Privacy / Other, with "inappropriate or harmful" under Other                                                                                              | "Inappropriate or harmful" moves to priority 2 with privacy; chip **Privacy/Harm** (orange) replaces **Privacy**; **Other** (grey) is only "something else" (PO decision Q44, 2026-11-19).                        |
| C7  | Photos canvas title mentions "consent"                                                                                                                                              | No consent tick (PO decision 2026-11-18). The upload records who and when.                                                                                                                                        |
| C8  | F7 shows a "Top this week" tab                                                                                                                                                      | One **Top** tab with a period dropdown: Top this week (last 7 days) · Top this month (last 30 days) · Top this year (last 365 days) (PO decision Q26, 2026-11-19).                                                |

---

## 1. Scope

### In (PRD references)

1. **Feed (PRD §6.5):** Home for signed-in users with tabs **Nearby** (Bangalore, newest first), **Following** and **Top** with a period dropdown: this week / this month / this year, rolling windows of the last 7 / 30 / 365 days (likes + 3 × comments of deeds published in the window, display order only; Q26); post cards, a deed page with comments, a public share page.
2. **Publishing:** verified deeds are published automatically; withdrawn, rejected (and later flagged or removed) deeds leave the feed; self-logs, pending and unverified deeds never appear. Area name only.
3. **Visibility:** the doer or reporter can hide a deed and show it again; hiding turns its share link off; visibility never touches status, counts, points or prize fields.
4. **Social:** likes; comments (300 characters, rate-limited, kind-words filter; author deletes own; doer and reporter delete any on their deed; members report comments, moderators and admins remove them (Q38)); open one-way follows with remove-follower; private saves; share to WhatsApp, Copy link and the Web Share API, with each share recorded (PRD §8 `Share`).
5. **Photos (PRD §6.5 photos, §12):** photos only, at most 4 per deed or self-log (config); add or remove for 24 h after sending (config); no automatic blurring; no consent tick (who and when recorded); optional "Blur a face" applied by the server; small thumbnails; EXIF/GPS stripping; re-encoding and resizing; type and size validation; upload-then-confirm; orphan cleanup; signed URLs. Self-log photos are private and have no blur tool.
6. **Storage:** GCS Mumbai via the S3-compatible API; SeaweedFS locally.
7. **Photo reports:** anyone can report (members, and visitors on the public page, rate-limited by IP) with one of five reasons and optional details; no automatic hiding; priority queue (children, then privacy, then the rest); admin keep / remove / restore with a required note, audit-logged; the uploader sees the final outcome on the deed in My deeds; nobody is told about open reports or who reported.
8. **Minimal admin screens:** photo-report queue and review page; comment-report queue and review page (Q38).
9. **Profiles:** follower and following counts, Follow button, the other person's deeds, own followers / following lists, Saved list.

### Out (later milestones)

- Video (deferred by the PO); notifications for likes, comments, follows and report outcomes (M6; M3 emits the events); reporting posts and the wider moderation tools (M7; comment reporting and removal moved into M3 by PO decision Q38); blocking users (M7); flagged-deed hiding (M5 emits the flag; the feed state `held` is ready for it); the full admin area, config UI and audit viewer (M5); ads between posts (M8); analytics (M8).
- Automatic face detection or blurring (not wanted; the pipeline has a hook for it, §4.6).
- Rich link previews (photo, name or text of a deed) and search indexing of public pages: not built (PO decision Q40, 2026-11-19).
- Account deletion itself (D8 rule extended in §2.6).

---

## 2. Data model (database-specialist)

Conventions as in M2: `op.f()` constraint names, non-native enums with CHECK constraints (`_enum`), UUID primary keys, `timestamptz`. Next migration numbers follow `0012`.

| Revision                           | Contents                                                                                                                                                                                                                                                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `2026_11_19_0000-0013_deeds_v5.py` | `areas` table and seed; `deeds.area_id` + backfill; publish `deeds` v5 (drops `photos_enabled`)                                                                                                                                                                                                                                |
| `2026_11_19_0100-0014_media.py`    | `photos`, `photo_report_cases`, `photo_reports`, `photo_decisions` (append-only trigger); seed `media` v1                                                                                                                                                                                                                      |
| `2026_11_19_0200-0015_feed.py`     | `feed_posts`, `feed_likes`, `feed_comments`, `feed_saves`, `feed_shares`, `follows`, `feed_top_snapshots` (with `period`), `feed_top_entries`, `comment_report_cases`, `comment_reports`, `comment_decisions` (append-only trigger, Q38), `feed_notices_seen` (Q41); seed `feed` v1; backfill `feed_posts` from verified deeds |

### 2.1 Deeds module changes

**`areas`** (reference data, `deeds/models.py`)

| column    | type                                   | notes                                                                                                                                                                                   |
| --------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| id        | smallint pk                            |                                                                                                                                                                                         |
| name      | varchar(80) not null                   | display name, e.g. "Koramangala"                                                                                                                                                        |
| city      | varchar(40) not null                   | "Bangalore"                                                                                                                                                                             |
| boundary  | geography(MultiPolygon, 4326) not null |                                                                                                                                                                                         |
| source    | varchar(120) not null                  | dataset name, publisher, publication date and licence, e.g. "Greater Bengaluru ward boundaries, {publisher}, {date} ({licence})"; attribution shown on `/terms` if the licence needs it |
| ward_code | varchar(20) not null                   | the dataset's own ward ID, kept for audit and re-imports; **never returned by any API or shown**                                                                                        |

Index: GiST on `boundary`. Seeded by migration 0013 from `backend/app/modules/deeds/data/bangalore_areas.geojson` (checked in), built from the **latest officially published Greater Bengaluru ward-boundary dataset** (Q28 = A). Next to it, `data/AREAS_SOURCE.md` records the dataset's title, publisher, source URL, publication date, download date, licence and attribution text, and the name clean-up table. Task DATA1 does this **before** the seed is built: confirm the dataset is the current official one (the wards were redrawn after the Greater Bengaluru split), that its licence allows use and redistribution in an app, and whether attribution is needed (if so, add it to `/terms`). If no suitably licensed official dataset exists, stop and ask the PO; never fall back to scraping or a paid geocoder.

**Name clean-up (Q28):** each ward gets a familiar, simplified display name, e.g. "Koramangala", not "Ward 151 – Koramangala" and not "Koramangala 4th Block". Rules: no ward numbers or codes, no road, street, building or landmark names, title case, at most 40 characters. Two wards may share one familiar name (the result is only coarser). Nothing finer than a ward is ever stored as a name. The table is reviewed by a person (PO or delegate) before seeding.

**`deeds.area_id`** smallint null FK `areas` RESTRICT. Set in the same transaction as the deed is created (QR) or accepted (invite with a location): `SELECT id FROM areas WHERE ST_Covers(boundary, :loc) ORDER BY id LIMIT 1`; if none, the nearest area within `area_fallback_m` (config, 1500 m) by `ST_DWithin` + distance order; else null, shown as "Bangalore". **If the lookup fails for any reason** (no location, an invalid point, a database error), `area_id` stays null and the deed is still created; the name shows "Bangalore" rather than a guess, and the error is logged **without** coordinates. The lookup runs only in our own database (PostGIS); coordinates are never sent to any external geocoding, map or address service. Migration 0013 backfills existing deeds with the same rule. No new index on `deeds` (the lookup is by the deed's own point against `areas`).

**`deeds` config v5** (migration 0013, same `_publish_from_latest` pattern as 0007/0008): the latest values **minus** `photos_enabled` (moved to `media`, D41) **plus** `area_fallback_m: 1500`. `DeedLimits` drops `photos_enabled` and gains `area_fallback_m: int`. Downgrade appends the inverse. `GET /api/deeds/options` drops `photos_enabled` (the frontend reads `GET /api/media/options`).

### 2.2 `media` module (`backend/app/modules/media/models.py`)

**`photos`**: one row per upload, kept after deletion as the upload record.

| column                                 | type                                          | notes                                                                                                                                                                                                         |
| -------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| id                                     | uuid pk                                       | also the object-key stem; never derived from anything guessable                                                                                                                                               |
| uploader_user_id                       | uuid not null FK users RESTRICT               | who uploaded (PO: recorded instead of a consent tick)                                                                                                                                                         |
| purpose                                | varchar(20) not null                          | `deed` \| `self_log` (declared at create; attach must match)                                                                                                                                                  |
| deed_id                                | uuid null FK deeds RESTRICT                   | set at attach                                                                                                                                                                                                 |
| self_log_id                            | uuid null FK self_logs **ON DELETE SET NULL** | set at attach; deleting a self-log detaches its photos, and the cleanup job deletes their objects (self-logs still emit no events, D3)                                                                        |
| position                               | smallint null                                 | 1..`photos_per_owner`, carousel order                                                                                                                                                                         |
| status                                 | varchar(20) not null                          | `pending_upload` → `uploaded` → `queued` → `processing` → `ready`; or `rejected` (bad file), `failed` (processing gave up), `expired` (never attached), `deleted`                                             |
| takedown                               | varchar(20) not null default `none`           | `none` \| `admin_removed` (reversible) \| `doer_removed` (Q29 = A: final; nobody, not even an admin, can restore it, and its objects are deleted at once)                                                     |
| declared_type                          | varchar(40) not null                          | `image/jpeg` \| `image/png` \| `image/webp`                                                                                                                                                                   |
| declared_bytes                         | integer not null                              | CHECK `> 0`                                                                                                                                                                                                   |
| staging_key                            | varchar(200) not null                         | `staging/{id}`; the raw upload                                                                                                                                                                                |
| staging_deleted_at                     | timestamptz null                              | set when the raw object is deleted                                                                                                                                                                            |
| content_sha256                         | char(64) null                                 | SHA-256 of the raw upload bytes, set by the worker. Not an image; kept after deletion so a photo the doer removed can't be re-uploaded byte-for-byte to the same deed (Q29)                                   |
| variant_keys                           | jsonb null                                    | `{"thumb": "photos/{id}/thumb-{v}.webp", "display": "photos/{id}/display-{v}.webp"}`                                                                                                                          |
| width, height                          | integer null                                  | of the `display` variant (layout without reflow)                                                                                                                                                              |
| blur_regions                           | jsonb not null default `[]`                   | normalised ellipses applied (§4.5); numbers only. The admin page shows "No faces were blurred" / "{n} faces were blurred"                                                                                     |
| reject_reason                          | varchar(40) null                              | `unsupported_type`, `too_large`, `too_many_pixels`, `animated`, `corrupt`, `size_mismatch`, `removed_by_doer` (the same file as a photo the doer removed from this deed, Q29)                                 |
| attempts                               | smallint not null default 0                   | processing attempts                                                                                                                                                                                           |
| upload_ip_hash                         | varchar(64) null                              | `keyed_hash("ip", ip)`                                                                                                                                                                                        |
| created_at                             | timestamptz not null default now()            | upload started                                                                                                                                                                                                |
| uploaded_at, attached_at, processed_at | timestamptz null                              |                                                                                                                                                                                                               |
| processing_started_at                  | timestamptz null                              | stuck-job detection                                                                                                                                                                                           |
| deleted_at                             | timestamptz null                              |                                                                                                                                                                                                               |
| deleted_by                             | uuid null                                     | uploader, doer (Q29) or null = system                                                                                                                                                                         |
| objects_purged_at                      | timestamptz null                              | derivatives deleted from storage                                                                                                                                                                              |
| object_move                            | varchar(10) null                              | `hold` (admin removal: move variants to `held/`) \| `release` (restore: copy back under `photos/` with a new version) \| null when done; the move runs right after commit and `media.move_objects` retries it |
| removed_at                             | timestamptz null                              | last admin removal; null when restored                                                                                                                                                                        |

Constraints:

- `ck_photos_one_owner`: `NOT (deed_id IS NOT NULL AND self_log_id IS NOT NULL)`
- `ck_photos_owner_matches_purpose`: `(deed_id IS NULL OR purpose = 'deed') AND (self_log_id IS NULL OR purpose = 'self_log')`
- `ck_photos_takedown_deed_only`: `takedown = 'none' OR purpose = 'deed'`
- `ck_photos_ready_has_variants`: `status <> 'ready' OR (variant_keys IS NOT NULL AND width IS NOT NULL)`
- `uq_photos_deed_position UNIQUE (deed_id, position) WHERE status IN ('queued','processing','ready') AND takedown = 'none'` (likewise `uq_photos_self_log_position`).

Indexes:

- `(deed_id, position) WHERE deed_id IS NOT NULL` — photos for a page of deeds
- `(self_log_id) WHERE self_log_id IS NOT NULL`
- `(uploader_user_id, created_at DESC)` — pending-upload count, upload history
- `(status, created_at) WHERE status IN ('pending_upload','uploaded')` — expiry sweep
- `(status, processing_started_at) WHERE status IN ('queued','processing')` — work queue
- `(deleted_at) WHERE objects_purged_at IS NULL AND status = 'deleted'` — purge retries

**`photo_report_cases`**: at most one open case per photo.

| column         | type                        | notes                                                                                                                                                                                     |
| -------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| id             | uuid pk                     |                                                                                                                                                                                           |
| photo_id       | uuid not null FK photos     |                                                                                                                                                                                           |
| status         | varchar(20) not null        | `open` \| `resolved` \| `closed` (photo deleted by its uploader or doer while open)                                                                                                       |
| priority       | smallint not null           | 1 = child (`child_no_guardian_consent`), 2 = privacy and harm, equal (`not_consented`, `private_details`, `inappropriate`; Q44), 3 = other (`other`); the minimum over the case's reports |
| report_count   | integer not null default 0  |                                                                                                                                                                                           |
| opened_at      | timestamptz not null        | first report                                                                                                                                                                              |
| resolved_at    | timestamptz null            |                                                                                                                                                                                           |
| decision       | varchar(20) null            | `kept` \| `removed`                                                                                                                                                                       |
| removal_reason | varchar(30) null            | the reason shown to the uploader (admin's choice, defaults to the top-priority report reason)                                                                                             |
| decided_by     | uuid null FK users RESTRICT |                                                                                                                                                                                           |

Constraints: `uq_photo_report_cases_open UNIQUE (photo_id) WHERE status = 'open'`; CHECK `(status = 'resolved') = (decision IS NOT NULL AND resolved_at IS NOT NULL AND decided_by IS NOT NULL)`.
Indexes: `(status, priority, opened_at)` (queue order); `(photo_id, resolved_at DESC)` (latest outcome).

**`photo_reports`**

| column           | type                                | notes                                                                                     |
| ---------------- | ----------------------------------- | ----------------------------------------------------------------------------------------- |
| id               | uuid pk                             |                                                                                           |
| case_id          | uuid not null FK photo_report_cases |                                                                                           |
| photo_id         | uuid not null FK photos             |                                                                                           |
| reporter_user_id | uuid null FK users RESTRICT         | null = visitor                                                                            |
| reporter_ip_hash | varchar(64) not null                | keyed hash, for visitors and members                                                      |
| reason           | varchar(30) not null                | `not_consented`, `child_no_guardian_consent`, `private_details`, `inappropriate`, `other` |
| details          | varchar(500) null                   | free text; admins only; length from config (`report_details_max`)                         |
| created_at       | timestamptz not null default now()  |                                                                                           |

Constraints: `uq_photo_reports_case_member UNIQUE (case_id, reporter_user_id) WHERE reporter_user_id IS NOT NULL`; `uq_photo_reports_case_visitor UNIQUE (case_id, reporter_ip_hash) WHERE reporter_user_id IS NULL`. A duplicate gets the same 202 answer and creates nothing.
Index: `(case_id, created_at)`.

**`photo_decisions`** (append-only; trigger `photo_decisions_append_only` using `forbid_modification()`)

| column         | type                               | notes                                  |
| -------------- | ---------------------------------- | -------------------------------------- |
| id             | uuid pk                            |                                        |
| photo_id       | uuid not null FK photos            |                                        |
| case_id        | uuid null FK photo_report_cases    | null for a restore made outside a case |
| action         | varchar(20) not null               | `keep`, `remove`, `restore`            |
| removal_reason | varchar(30) null                   | for `remove`                           |
| note           | text not null                      | CHECK `length(btrim(note)) >= 5`       |
| actor_user_id  | uuid not null FK users RESTRICT    |                                        |
| created_at     | timestamptz not null default now() |                                        |

Index: `(photo_id, created_at)`. Every row is also written to `audit_log` (`entity_type="photo"`, `action="photo.kept" | "photo.removed" | "photo.restored"`, `reason=note`).

### 2.3 `feed` module (`backend/app/modules/feed/models.py`)

**`feed_posts`**: the projection (D37, D38). One row per deed that has ever been verified.

| column                | type                                                                                                                           | notes                                                                                                                                                              |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| deed_id               | uuid pk FK deeds RESTRICT                                                                                                      |                                                                                                                                                                    |
| doer_id               | uuid not null FK users RESTRICT                                                                                                |                                                                                                                                                                    |
| reporter_id           | uuid not null FK users RESTRICT                                                                                                |                                                                                                                                                                    |
| published_at          | timestamptz not null                                                                                                           | the deed's `verified_at`; feed order                                                                                                                               |
| state                 | varchar(20) not null                                                                                                           | `published`, `withdrawn`, `rejected`, `removed` (M7), `held` (M5 flags). Terminal states never go back to `published`                                              |
| hidden_by_doer_at     | timestamptz null                                                                                                               |                                                                                                                                                                    |
| hidden_by_reporter_at | timestamptz null                                                                                                               | (two columns: separate hides, PO decision Q24 = A)                                                                                                                 |
| listed                | boolean **generated always as** `(state = 'published' AND hidden_by_doer_at IS NULL AND hidden_by_reporter_at IS NULL)` stored |                                                                                                                                                                    |
| like_count            | integer not null default 0                                                                                                     | CHECK `>= 0`; every like, including the doer's and reporter's (shown everywhere)                                                                                   |
| top_like_count        | integer not null default 0                                                                                                     | CHECK `>= 0 AND top_like_count <= like_count`; likes from anyone **except** the deed's doer and reporter; used only by the Top score (PO decision Q43, 2026-11-19) |
| comment_count         | integer not null default 0                                                                                                     | live (not deleted) comments; CHECK `>= 0`                                                                                                                          |
| synced_at             | timestamptz not null                                                                                                           | last `sync_post`                                                                                                                                                   |

Indexes (all for keyset order `published_at DESC, deed_id DESC`):

- `ix_feed_posts_listed (published_at DESC, deed_id DESC) WHERE listed` — Nearby, Top snapshot input
- `ix_feed_posts_doer_listed (doer_id, published_at DESC, deed_id DESC) WHERE listed` — Following, profile deeds
- `ix_feed_posts_reporter_listed (reporter_id, published_at DESC, deed_id DESC) WHERE listed` — Following (if Q25 includes reporters)

**`feed_likes`**: `deed_id` FK feed_posts, `user_id` FK users, `created_at`; PK `(deed_id, user_id)`; index `(user_id, created_at DESC)`.

**`feed_comments`**

| column                 | type                               | notes                                                                                                     |
| ---------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------- |
| id                     | uuid pk                            |                                                                                                           |
| deed_id                | uuid not null FK feed_posts        |                                                                                                           |
| author_id              | uuid not null FK users RESTRICT    |                                                                                                           |
| body                   | varchar(1000) null                 | schema ceiling; the product limit (300) is config. Set to NULL when erased (§3.4 #9, Q38)                 |
| created_at             | timestamptz not null default now() |                                                                                                           |
| deleted_at             | timestamptz null                   |                                                                                                           |
| deleted_by             | uuid null                          |                                                                                                           |
| deleted_role           | varchar(20) null                   | `author`, `doer`, `reporter`, `moderation` (a moderator or admin decision on a report, Q38)               |
| removal_notice_seen_at | timestamptz null                   | when the author dismissed the "comment removed" notice (moderation removals only, PO decision 2026-11-19) |

CHECK `(deleted_at IS NULL) = (deleted_by IS NULL)`. Indexes: `(deed_id, created_at, id) WHERE deleted_at IS NULL`; `(author_id, created_at DESC)`; `(deleted_at) WHERE body IS NOT NULL AND deleted_at IS NOT NULL` (erasure job).

**Text retention (PO decision Q38, 2026-11-19):** a comment deleted by its **author** has `body` set to NULL in the same transaction (erased for good). A comment deleted by the deed's **doer or reporter**, or removed by a **moderator or admin**, disappears for everyone at once, and its text is kept for `deleted_comment_retention_days` (30), visible to moderators and admins only, then erased by `feed.erase_deleted_comments`.

**`comment_report_cases`** (Q38): at most one open case per comment. `id` uuid pk; `comment_id` FK feed_comments; `deed_id` (denormalised for the conflict check); `status` `open` | `resolved` | `closed` (comment deleted by its author, doer or reporter while open); `priority` smallint (1 = `threat_or_harassment`, `personal_info`; 2 = `hate`; 3 = `spam`, `other`; the minimum over the case's reports); `report_count`; `opened_at`; `resolved_at`; `decision` `kept` | `removed`; `decided_by` FK users. Constraints and indexes as `photo_report_cases`: `UNIQUE (comment_id) WHERE status = 'open'`; resolved ⇔ decision, time and decider set; `(status, priority, opened_at)`.

**`comment_reports`**: `id`; `case_id` FK; `comment_id`; `reporter_user_id` not null FK users (members only: visitors can't see comments); `reason` (`threat_or_harassment`, `hate`, `personal_info`, `spam`, `other`); `details` varchar(500) null (moderators and admins only); `created_at`. `UNIQUE (case_id, reporter_user_id)`; a duplicate gets the same 202.

**`comment_decisions`** (append-only, `forbid_modification()` trigger): `id`, `comment_id`, `case_id` null, `action` (`keep`, `remove`, `restore`), `note` (≥ 5 characters), `actor_user_id`, `created_at`. Each row is also written to `audit_log` (`entity_type="comment"`, `action="comment.kept"|"comment.removed"|"comment.restored"`).

**`feed_saves`**: `user_id`, `deed_id`, `created_at`; PK `(user_id, deed_id)`; index `(user_id, created_at DESC, deed_id DESC)`. Never counted or shown to anyone else.

**`feed_shares`**: `id`, `deed_id`, `user_id` (null for a visitor share from the public page, if allowed later), `target` (`whatsapp`, `copy_link`, `web_share`), `created_at`. Index `(deed_id)`. Analytics only (M8); no count is shown.

**`follows`**: `follower_id`, `followed_id` (both FK users RESTRICT), `created_at`; PK `(follower_id, followed_id)`; CHECK `follower_id <> followed_id`; index `(followed_id, created_at DESC)`; the PK also serves "who do I follow" ordered by… add `(follower_id, created_at DESC)` for the Following list.

**`feed_notices_seen`** (Q41): `user_id` FK users, `notice_key` varchar(40), `seen_at`; PK `(user_id, notice_key)`. Known keys are a code constant (`public_feed_intro`).

**`feed_top_snapshots`**: `id` uuid pk, `period` varchar(10) not null CHECK `IN ('week','month','year')`, `computed_at`, `window_start`, `weights` jsonb (`{"like":1,"comment":3}` as used); index `(period, computed_at DESC)`. **`feed_top_entries`**: `snapshot_id` FK (CASCADE), `rank` smallint, `deed_id`, `score` integer; PK `(snapshot_id, rank)`. The job keeps the newest two snapshots **per period**, so a reader holding the previous snapshot's cursor can finish. No new index on `feed_posts`: every period's job is a range scan of `ix_feed_posts_listed` on `published_at`.

**Backfill (0015):** insert a `feed_posts` row (`state='published'`, `published_at=verified_at`) for every deed with `status='verified'`. M2 data is dev-only, but this keeps local databases consistent.

### 2.4 Identity

No schema change. A CLI command sets roles (D43, task B22): `uv run python -m app.modules.identity.cli set-role <handle> admin|moderator|member --reason "..."`, implemented as `identity.service.set_role(session, user, role, *, actor_id=None, reason)`, writing `audit_log` (`entity_type="user"`, `action="role.changed"`) and bumping `token_version` (so an old access token can't keep a removed role).

### 2.5 Config namespaces (seed v1)

**`media` v1** (`media/_limits.py:MediaLimits`, no defaults):

```json
{
  "photos_enabled": true,
  "photos_per_owner": 4,
  "photo_edit_window_hours": 24,
  "upload_allowed_types": ["image/jpeg", "image/png", "image/webp"],
  "upload_max_bytes": 10485760,
  "upload_url_ttl_seconds": 600,
  "pending_upload_ttl_hours": 24,
  "max_pending_uploads_per_user": 12,
  "max_image_pixels": 40000000,
  "min_image_side_px": 200,
  "variants": [
    { "name": "thumb", "max_side_px": 320 },
    { "name": "display", "max_side_px": 1280 }
  ],
  "webp_quality": 80,
  "blur_max_regions": 8,
  "blur_min_region": 0.02,
  "processing_max_attempts": 3,
  "processing_stuck_minutes": 10,
  "url_ttl_minutes": 60,
  "url_step_minutes": 30,
  "report_details_max": 500,
  "decision_note_max": 1000,
  "report_outcome_display_days": 30,
  "removed_photo_retention_days": 90,
  "rate_upload_user": "40/3600",
  "rate_upload_ip": "200/3600",
  "rate_photo_write_user": "60/3600",
  "rate_report_user": "10/3600",
  "rate_report_ip": "10/3600",
  "rate_report_ip_daily": "30/86400",
  "rate_admin_decision_user": "300/3600"
}
```

`removed_photo_retention_days` = 90 (PO decision Q32, 2026-11-19). `report_outcome_display_days` = 30 (PO decision Q39, 2026-11-19).

**`feed` v1** (`feed/_limits.py:FeedLimits`):

```json
{
  "comment_max_chars": 300,
  "comment_block_links": true,
  "comment_block_phone_numbers": true,
  "comment_filter_enabled_languages": ["en"],
  "comment_filter_languages": {
    "en": {
      "blocked_terms": ["…English starter list, see §3.6…"],
      "substitutions": {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s"
      },
      "keep_marks": [],
      "squashed_min_letters": 5
    }
  },
  "deleted_comment_retention_days": 30,
  "top_week_days": 7,
  "top_month_days": 30,
  "top_year_days": 365,
  "top_like_weight": 1,
  "top_comment_weight": 3,
  "top_size": 50,
  "top_badge_ranks": 3,
  "top_week_refresh_minutes": 10,
  "top_month_refresh_minutes": 30,
  "top_year_refresh_minutes": 60,
  "max_following": 2000,
  "rate_comment_user": "5/60",
  "rate_comment_user_daily": "100/86400",
  "rate_comment_ip": "60/3600",
  "rate_comment_report_user": "10/3600",
  "rate_like_user": "120/3600",
  "rate_like_ip": "600/3600",
  "rate_save_user": "120/3600",
  "rate_follow_user": "60/3600",
  "rate_follow_ip": "300/3600",
  "rate_hide_user": "30/86400",
  "rate_share_user": "60/3600",
  "rate_public_deed_ip": "120/900",
  "rate_feed_read_user": "600/900"
}
```

`comment_block_links` and `comment_block_phone_numbers` are both `true` (PO decision Q37, 2026-11-19); `deleted_comment_retention_days` = 30 (PO decision Q38, 2026-11-19). The `rate_comment_user` copy is from the canvas: "You're commenting very fast — try again in a minute".

**PRD limits table:** after PO OK, add rows for comment length (300), Top weights (1 / 3), windows (7 / 30 / 365 days, rolling) and refresh intervals (10 / 30 / 60 min), photo size (10 MB) and signed-URL lifetime (60 min). Task DOC3.

### 2.6 Notes

- **Account deletion (D8, when built):** follows, likes and saves of the deleted user are deleted; their comments are erased (`body = NULL`, shown as "Deleted comment"); photos they uploaded on deeds stay (the deed is anonymised, not deleted; the upload record points at the anonymised user); self-log photos go with the self-logs. Recorded here so M3's schema needs no change later.
- **Pending deeds** never get a `feed_posts` row (D37 sync only publishes `verified`).
- **Photos of a withdrawn deed** are not served to anyone but the reporter (owner view). They are not deleted, because withdrawal keeps the deed as evidence.

---

## 3. API contract

### 3.1 Conventions and `deeds.service` additions

- Routers: `backend/app/modules/media/router.py`, `backend/app/modules/feed/router.py`, mounted under `/api`; both modules added to `modules/registry.py` (`"media"`, `"feed"`, in that order after `deeds`).
- Errors `{detail, code}` via `AppError`. Non-owners and anyone who may not see an item get **404**, never 403 (except role checks on `/api/admin`, which return 403 `forbidden` as `require_role` does today).
- Rate limits before any lookup; account key and IP key per action (M2 D1); specs from config.
- Every service function takes `now: datetime | None = None`.
- `UserRef = {handle, display_name}` is the only user information in any response (no IDs, phones, emails). `PhotoOut` URLs are the only storage information. **No response contains coordinates.**
- Cursors: a shared helper. `deeds/_cursor.py` is private to `deeds`, so `feed` and `media` each get their own `_cursor.py` (copy of the same 30 lines) rather than moving it into core. _(Alternative: move it to `app/core/pagination.py`; the backend developer may do this as a no-behaviour-change refactor.)_

New in `deeds/service.py`:

```python
async def get_deeds(session, deed_ids: Iterable[UUID]) -> dict[UUID, DeedView]   # one query, joins areas
async def self_log_owner(session, self_log_id: UUID) -> UUID | None              # owner only; no content
def area_name(view: DeedView) -> str                                           # view.area_name or "Bangalore"
# DeedView gains: area_name: str | None
```

`DeedOut` (`/api/deeds/{id}`, `/api/me/deeds`) and `ScanOut` gain `area_name: string` (the photos canvas shows "Verified just now · Koramangala" on the form; the scan location gives it). `GET /api/deeds/options` drops `photos_enabled`.

### 3.2 Shapes

```
PhotoOut   = { id, thumb_url, display_url, width, height, urls_expire_at }
PostOut    = { deed_id, doer: UserRef, reporter: UserRef, category, area_name,
              occurred_at, published_at, description, photos: PhotoOut[],
              like_count, comment_count, liked: bool, saved: bool,
              following_doer: bool, my_role: "doer"|"reporter"|null,
              rank: int|null,                 // Top tab only; shown as a badge when ≤ top_badge_ranks
              share_url }                     // {web_base_url}/d/{deed_id}
PostDetail = PostOut & { beneficiaries_count|null, verification: "qr_scan",
              visibility: { listed: bool, hidden_by_me: bool, hidden_by_other: bool,
                            can_hide: bool, can_show: bool } | null }   // null unless doer/reporter
CommentOut = { id, author: UserRef, body, created_at, mine: bool, can_delete: bool }
PublicDeed = { deed_id, doer: UserRef, reporter: UserRef, category, area_name, occurred_at,
              description, photos: PhotoOut[], like_count, comment_count }
SocialOut  = { followers: int, following: int, is_following: bool|null, is_me: bool }
OwnerPhoto = { id, position, status: "processing"|"ready"|"failed",
              thumb_url|null, display_url|null, blurred_count, can_remove: bool }
Outcome    = { decision: "removed"|"kept"|"removed_by_doer", removal_reason|null,
              removed_by: UserRef|null, decided_at, photo_count }   // removed_by only for removed_by_doer (Q29)
```

`description` is the deed text exactly as the reporter wrote it (M2 asked them to leave out phone numbers and addresses); nothing else of the deed is exposed. `rank` is null outside the Top tab.

### 3.3 Media endpoints (`media` module)

**1. `GET /api/media/options`**: Onboarded. `{photos_enabled, photos_per_owner, photo_edit_window_hours, upload_allowed_types, upload_max_bytes, blur_max_regions, report_reasons: [{id, label, priority}]}`. The frontend renders photo controls only when `photos_enabled` (as M2 D23 did with the deeds flag).

**2. `POST /api/media/uploads`**: Onboarded; rate `upload:u` + `upload:ip`.
Request `{purpose: "deed"|"self_log", content_type, size_bytes}`.

- 403 `photos_disabled` when off.
- 400 `unsupported_type` unless `content_type` ∈ `upload_allowed_types`; 400 `too_large` if `size_bytes > upload_max_bytes`.
- 429 `too_many_pending_uploads` when the user has ≥ `max_pending_uploads_per_user` rows in `pending_upload`/`uploaded`.
- Creates `photos` (`pending_upload`, `staging_key = staging/{id}`).
  `201`: `{photo_id, upload: {url, method: "PUT", headers: {"Content-Type": ..., "Content-Length": ...}}, expires_at}`. The URL is signed for `PUT` with `Content-Type` and `Content-Length` as signed headers, so the storage refuses any other type or length.

**3. `POST /api/media/uploads/{photo_id}/complete`**: uploader only (else 404).

- `HEAD staging_key`: missing → 409 `upload_missing`; `ContentLength ≠ declared_bytes` → 400 `size_mismatch` (row `rejected`, object deleted).
- Ranged GET of the first 16 bytes; magic number must match the declared type (JPEG `FF D8 FF`, PNG `89 50 4E 47 0D 0A 1A 0A`, WebP `RIFF????WEBP`), else 400 `unsupported_type` (row `rejected`, object deleted).
- Sets `uploaded`, `uploaded_at`. Idempotent: a second call on an `uploaded` row returns 200 unchanged.
  `200`: `{photo_id, status: "uploaded"}`.

**4. `DELETE /api/media/uploads/{photo_id}`**: uploader only; only while not attached. Deletes the staging object, row `expired`. 204.

**5. `POST /api/media/deeds/{deed_id}/photos`**: Onboarded, the deed's **reporter** only (else 404); rate `photowrite:u`.
Request `{photos: [{photo_id, blur_regions: [{x, y, w, h}]}]}` (1..`photos_per_owner` items; regions normalised, §4.5).
In one transaction:

1. `deeds.service.get_deed`: status must be `pending` or `verified` (else 409 `deed_not_editable`); `now ≤ reported_at + photo_edit_window_hours` (else 409 `photo_window_closed`).
2. `advisory_xact_lock("media_owner", deed_id)`; count photos on the deed with `status IN ('queued','processing','ready') AND takedown = 'none'`; if count + new > `photos_per_owner` → 409 `too_many_photos`.
3. Each photo: uploader = caller, `purpose = 'deed'`, `status = 'uploaded'` (else 409 `photo_not_uploaded`); validate regions (count ≤ `blur_max_regions`, each inside [0,1], `w,h ≥ blur_min_region`; else 422).
4. Set `deed_id`, next free `position`s, `blur_regions`, `attached_at`, `status = 'queued'`.
   `200`: `{photos: OwnerPhoto[]}` (new ones `processing`).
   The worker picks up `queued` rows within seconds (§4.4).

**6. `POST /api/media/self-logs/{self_log_id}/photos`**: owner only (`deeds.service.self_log_owner`), else 404. Same as #5 except: no time window (self-logs are editable at any time, M2 D3), `purpose = 'self_log'`, `blur_regions` must be empty (no blur tool on private photos; 422 otherwise).

**7. `GET /api/media/deeds/{deed_id}/photos`**: the reporter, or the doer when the deed is `verified` (M2 D24), else 404.
`200`: `{photos: OwnerPhoto[], can_add: bool, add_until: datetime|null, outcomes: Outcome[]}`.

- Lists `queued`/`processing` (as `processing`), `ready` and `failed` photos with `takedown = 'none'`. Admin-removed photos are not listed (not even to the uploader).
- `outcomes`: resolved cases on this deed's photos, and the doer's photo removals (Q29, shown to the reporter only), within `report_outcome_display_days`, newest first, grouped by decision. **Open cases are never reflected in any field** (no count, no flag, no change in ordering).
- `can_add` only for the reporter, inside the window, under the photo limit. `can_remove` is true for the uploader on their own photos at any time (Q30) and for the doer on every `ready` photo of their verified deed (Q29).

**8. `GET /api/media/self-logs/{self_log_id}/photos`**: owner only. `{photos: OwnerPhoto[]}`.

**9. `DELETE /api/media/photos/{photo_id}`**: Onboarded; rate `photowrite:u`.

- Uploader on a deed: **any time** (Q30 = A); only _adding_ is limited to the 24-hour window. Removing never changes the deed, its verification, counts, points or prizes. Uploader on a self-log: any time.
- **Doer of a verified deed (Q29 = A):** any `ready` photo on that deed, whoever uploaded it, at any time. Sets `takedown = 'doer_removed'` as well (final). Removes only that photo: the other photos stay, keep their order, and the deed stays listed. Writes `audit_log` (`entity_type="photo"`, `action="photo.doer_removed"`, actor = the doer, `new_value={"deed_id", "position", "uploader_id"}`, no image) and emits `PhotoRemovedByDoer`. Never touches `deeds`, `feed_posts`, counts, verification, the reporter, points or prize fields.
- Anyone else (another member, the reporter on a photo they didn't upload, an admin using this endpoint, the doer on a self-log that isn't theirs): 404. Admins act only through the report decision endpoints.
- Sets `status = 'deleted'`, `deleted_at`, `deleted_by`; closes any open case on the photo (`status = 'closed'`); deletes **every** object (variants and any staging copy) right after commit, so every URL already issued for it returns 404 from storage at once; failures are retried by `media.purge_deleted` every 5 minutes. 204.
- A doer-removed photo never comes back: no endpoint restores it (admin restore → 409 `not_restorable`), and processing rejects a new upload to the same deed whose `content_sha256` equals a doer-removed photo's (`reject_reason = 'removed_by_doer'`). A doer-removed photo doesn't count toward the photo limit, so the reporter may add a different photo inside the 24-hour window; the doer can remove that one too.

**10. `POST /api/media/photos/{photo_id}/reports`**: **auth optional**; rates: member `report:u` + `report:ip`; visitor `report:ip` + `report:ipd` (daily).
Request `{reason, details?: string (≤ report_details_max)}`.

1. Rate limits.
2. The photo must be `ready`, `takedown = 'none'`, `purpose = 'deed'`, and its deed viewable by the caller: for a visitor, `feed.service.is_public(deed_id)`; for a member, `feed.service.can_view(deed_id, user_id)`. Otherwise 404 `photo_not_found` (self-log photos are never reportable).
3. The uploader reporting their own photo: 400 `own_photo` (the menu hides the option anyway).
4. `advisory_xact_lock("photo_case", photo_id)`; find or create the open case; insert the report unless the same member (or, for visitors, the same IP hash) already reported in this case; update `priority = min(...)` and `report_count`.
   `202`: `{status: "received"}`, identical for a new report and a duplicate. The photo, deed and counts are untouched (D42).

**Admin (`/api/admin/photo-reports` and `/api/admin/photos/{id}/restore`, `require_role("moderator", "admin")` per Q34, rate `admindecision:u` on writes):**

**11. `GET /api/admin/photo-reports?status=open|resolved&cursor=`**: open ordered by `(priority, opened_at, id)`; resolved by `(resolved_at DESC, id)`. Items: `{case_id, priority, top_reason, report_count, opened_at, oldest_report_at, photo_thumb_url, deed: {doer: UserRef, category, area_name}}`. Response also carries `{open_count, resolved_count}` for the tab labels.

**12. `GET /api/admin/photo-reports/{case_id}`**: `{case, photo: {display_url, position, photo_count, uploaded_at, uploader: UserRef, blurred_count, state: "visible"|"removed"}, deed: {doer, reporter, category, area_name, status, listed}, reports: [{reason, details, reporter: {kind: "member", handle} | {kind: "visitor"}, created_at}], decisions: [{action, note, actor: UserRef, created_at}]}`. Admins see the photo even after removal (an admin-only signed URL for the `held/` key, issued only by this endpoint) because they may restore it. `deed.listed` comes from `feed.service` (C6).

**13. `POST /api/admin/photo-reports/{case_id}/decision`**: `{action: "keep"|"remove", note, removal_reason?}`.

- Note required (5..`decision_note_max` chars) → 422.
- Case must be `open` → 409 `case_not_open`.
- Conflict of interest (D43): actor is the deed's doer or reporter, or the photo's uploader → 403 `conflict_of_interest`. The same check applies to restore (#14).
- `remove`: `photos.takedown = 'admin_removed'`, `removed_at = now`, `object_move = 'hold'`. After commit the variants are copied to `held/{id}/…` (same private bucket) and the `photos/{id}/…` originals deleted, so **every URL already issued for the photo returns 404 at once**, while the photo stays restorable. `keep`: nothing changes on the photo.
- Case `resolved` with decision, `decided_by`, `removal_reason`; `photo_decisions` row; `audit_log`; emit `PhotoModerationDecided`.
  `200`: the case detail.

**14. `POST /api/admin/photos/{photo_id}/restore`**: `{note}`. Only when `takedown = 'admin_removed'` and objects not yet purged (else 409 `not_restorable`). Sets `takedown = 'none'`, `removed_at = NULL`, `object_move = 'release'` (after commit, variants are copied back to `photos/{id}/…` under a **new version suffix**, so old URLs stay dead and new ones are issued; the photo is listed again once the copy completes), writes `photo_decisions(action='restore', case_id = the last case)`, audit, event. The uploader's outcome then reads "kept" (the latest decision wins).

| Status | code                                                                                                                                     | Where       |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| 400    | `unsupported_type`, `too_large`, `size_mismatch`, `own_photo`                                                                            | #2, #3, #10 |
| 403    | `photos_disabled`, `forbidden`, `conflict_of_interest`                                                                                   | #2, admin   |
| 404    | `photo_not_found`, `deed_not_found`, `self_log_not_found`, `case_not_found`                                                              | all         |
| 409    | `upload_missing`, `photo_not_uploaded`, `too_many_photos`, `photo_window_closed`, `deed_not_editable`, `case_not_open`, `not_restorable` |             |
| 422    | validation (regions, note, reason)                                                                                                       |             |
| 429    | `rate_limited`, `too_many_pending_uploads`                                                                                               |             |

### 3.4 Feed endpoints (`feed` module)

Reads need `CurrentUser`; writes need `OnboardedUser` (D44). Every list returns `{items, next_cursor}`; page size 20, max 50 (code constants, as in M2).

**1. `GET /api/feed?tab=nearby|following|top&period=week|month|year&cursor=`** (`period` only with `tab=top`, default `week`; anything else 422)

- **nearby:** `WHERE listed AND (published_at, deed_id) < (:t, :id) ORDER BY published_at DESC, deed_id DESC` (index `ix_feed_posts_listed`).
- **following:** same plus `doer_id IN (SELECT followed_id FROM follows WHERE follower_id = :me)` `OR reporter_id IN (…)` (the reporter part included: PO decision Q25 = A). If the planner picks poorly with many follows, use a `UNION ALL` of the two indexed scans with `LIMIT` each, then merge.
- **top:** read the newest `feed_top_snapshots` row for the requested `period` (or the one named in the cursor; a cursor from another period is 400), entries ordered by `rank`; cursor = `(snapshot_id, rank)`. Entries whose post is no longer `listed` are skipped at read time. The snapshot job runs once per period (every `top_{period}_refresh_minutes`): `score = top_like_weight × top_like_count + top_comment_weight × comment_count` (the same formula and weights for every period) over posts `WHERE listed AND published_at >= now − top_{period}_days` (a **rolling** window, not a calendar month or year), `ORDER BY score DESC, published_at DESC, deed_id DESC LIMIT top_size`. `like_count` and `comment_count` are the post's **totals**, not only engagement inside the window; a deed published before the window never appears, however many new likes or comments it gets (Q26 = A, extended to week / month / year by the PO on 2026-11-19). Reads never compute scores, so the period costs nothing per request; the year job scans at most a year of listed posts (about 365k rows at 1,000 deeds a day), well under a second, once an hour.
- **Composition** (same for every list): the page's IDs → `deeds.service.get_deeds` (drop any not `verified`, D37) → `identity.service.public_summaries` (doer + reporter) → `media.service.public_photos(deed_ids)` (ready, not taken down, signed URLs) → the caller's likes, saves and follows for those IDs (three indexed queries). About seven queries per page.
- Excludes nothing else in M3 (blocking is M7).

**2. `GET /api/feed/deeds/{deed_id}`**: `PostDetail`. Viewable when `listed`, or when the caller is the doer or reporter and the post is `published` but hidden. Otherwise 404 `deed_not_found` (the same for a deed that doesn't exist).

**3. `PUT /api/feed/deeds/{deed_id}/like`** and **`DELETE …/like`**: rate `like:u` + `like:ip`. Viewable as in #2 (else 404). `INSERT … ON CONFLICT DO NOTHING RETURNING`; only a real insert increments `like_count` (and emits `DeedLiked`); only a real delete decrements it. A like from someone other than the deed's doer or reporter also increments/decrements `top_like_count` in the same statement (Q43). `200 {liked, like_count}`. Idempotent. **The doer and reporter can like their own deed** (PO decision Q43, 2026-11-19): recorded normally, counted in `like_count` and shown, but never in `top_like_count`, so it adds nothing to the Top score.

**4. `PUT /api/feed/deeds/{deed_id}/save`** and **`DELETE …/save`**: rate `save:u`. `200 {saved}`. No count anywhere.

**5. `GET /api/feed/deeds/{deed_id}/comments?cursor=`**: oldest first, keyset `(created_at, id) >`; live comments only; `can_delete` = author, or the caller is the deed's doer or reporter.

**6. `POST /api/feed/deeds/{deed_id}/comments`** `{body}`: rate `comment:u` (`5/60`), `comment:ud` (daily), `comment:ip`.

- Trim; empty → 422; length > `comment_max_chars` → 422 `comment_too_long`.
- Kind-words filter (§3.6) → 422 `comment_not_kind`.
- Links (if `comment_block_links`) → 422 `comment_has_link`; phone numbers (if `comment_block_phone_numbers`) → 422 `comment_has_phone`.
- Insert; `comment_count += 1`; emit `CommentPosted`. `201 CommentOut`.

**7. `DELETE /api/feed/comments/{comment_id}`**: the author, or the doer or reporter of the comment's deed; anyone else 404. Sets `deleted_at/by/role`; `comment_count -= 1`; author deletions erase `body` at once, doer/reporter deletions keep it for `deleted_comment_retention_days` (Q38). Closes any open report case on the comment. Other members (and moderators or admins acting as members) → 404; moderators and admins remove comments only through a report decision (§3.4a). 204. Deleting twice: 204.

**8. `POST /api/feed/deeds/{deed_id}/hide`** and **`POST …/show`**: the doer or reporter only (404 otherwise); rate `hide:u`. Sets or clears the caller's own column (Q24). Writes `audit_log` (`entity_type="deed_visibility"`, `action="hidden"|"shown"`, `new_value={"role": "doer"|"reporter"}`). Only allowed while `state = 'published'` (else 409 `deed_not_published`). `200 PostDetail.visibility`. Never touches `deeds`, counts, points or prize fields.

**9. `POST /api/feed/deeds/{deed_id}/shares`** `{target: "whatsapp"|"copy_link"|"web_share"}`: rate `share:u`; viewable as in #2 and `listed` (a hidden deed has no share link, 409 `not_shareable`). 204. Only records the share.

**10. `GET /api/public/deeds/{deed_id}`**: **no auth**; rate `public:ip`. `PublicDeed` only when `listed` and the deed is live-checked `verified`; otherwise **404 `deed_unavailable`** with an identical body for missing, hidden, withdrawn, removed and never-existing deeds. Headers: `Cache-Control: no-store`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Robots-Tag: noindex, nofollow` (PO decision Q40).

**11. Profiles and follows:**

- `GET /api/users/{handle}/social`: public counts; `is_following` when signed in. Unknown or inactive handle 404. Rate `public:ip` when anonymous.
- `GET /api/feed/users/{handle}/deeds?cursor=`: listed deeds where the user is the doer, newest first (`ix_feed_posts_doer_listed`). CurrentUser.
- `PUT /api/users/{handle}/follow`, `DELETE …/follow`: rate `follow:u` + `follow:ip`. Self → 400 `cannot_follow_self`; at `max_following` → 409 `following_limit`; unknown/inactive → 404. Idempotent; a real insert emits `UserFollowed`. `200 SocialOut`.
- `DELETE /api/me/followers/{handle}`: removes that person's follow of me, quietly (no event, they aren't told). 204.
- `GET /api/me/followers?cursor=`, `GET /api/me/following?cursor=`: own lists only (PO decision Q36 = A); there is no endpoint for another person's lists, `{items: [{user: UserRef, following: bool}], next_cursor}`.

**12. `GET /api/me/saved?cursor=`**: saved posts, newest save first, composed as #1; a saved deed that is no longer viewable silently drops out of the list (the save row stays, so it returns if the deed is shown again).

| Status | code                                                                                 |
| ------ | ------------------------------------------------------------------------------------ |
| 400    | `cannot_follow_self`, `invalid_cursor`                                               |
| 403    | `onboarding_required` (writes)                                                       |
| 404    | `deed_not_found`, `deed_unavailable` (public), `comment_not_found`, `user_not_found` |
| 409    | `deed_not_published`, `not_shareable`, `following_limit`                             |
| 422    | `comment_too_long`, `comment_not_kind`, `comment_has_link`, `comment_has_phone`      |
| 429    | `rate_limited`                                                                       |

`feed/service.py` public surface: `is_public(session, deed_id) -> bool`, `can_view(session, deed_id, user_id) -> bool`, `visibility(session, deed_id) -> VisibilityView | None` (for the admin page). Scoring must never call these (D30).

### 3.4a Comment reports and removal (PO decision Q38, 2026-11-19)

**13. `POST /api/feed/comments/{comment_id}/reports`**: `OnboardedUser` (Q42 = A); rate `commentreport:u`. `{reason, details?}`. The comment must be live (not deleted) on a deed the caller can view, and not the caller's own (404 `comment_not_found` otherwise, same body). Opens or joins the comment's open case, raises its priority if needed. **Never hides the comment** and never tells the author. `202`, the same answer for a duplicate.

**Admin (`/api/admin/comment-reports` and `/api/admin/comments/{id}/restore`, `require_role("moderator", "admin")` as Q34, rate `admindecision:u` on writes):**

- **14. `GET /api/admin/comment-reports?status=open|resolved&cursor=`**: ordered like photo reports. Items: `{case_id, priority, top_reason, report_count, opened_at, oldest_report_at, comment_excerpt (first 80 chars), author: UserRef, deed: {doer, category, area_name}}`; `{open_count, resolved_count}`.
- **15. `GET /api/admin/comment-reports/{case_id}`**: the comment text (also after removal, while retained), author, created time, the deed (doer, reporter, category, area, listed), the reports (reason, details, member handle), decisions, and `author_removed_last_90_days` (how many of this author's comments were removed by moderation in the last 90 days, to spot patterns).
- **16. `POST /api/admin/comment-reports/{case_id}/decision`**: `{action: "keep"|"remove", note}`. Note required (422); case must be open (409 `case_not_open`); **conflict of interest**: the actor is the comment's author or the deed's doer or reporter → 403 `conflict_of_interest`. `remove`: `deleted_at = now`, `deleted_by = actor`, `deleted_role = 'moderation'`, `comment_count -= 1`, text kept for `deleted_comment_retention_days`. `keep`: no change. `comment_decisions` row, `audit_log`, `CommentModerationDecided`.
- **17. `POST /api/admin/comments/{comment_id}/restore`**: `{note}`. Only for `deleted_role = 'moderation'` while `body` is still kept (else 409 `not_restorable`); same conflict check. Clears the deletion, `comment_count += 1`, decision row, audit, event.
- **The author is told (PO decision 2026-11-19, follow-up to Q38):** `GET /api/feed/me/comment-notices` returns the caller's comments removed by moderation within `report_outcome_display_days` (30) whose `removal_notice_seen_at` is null: `{comment_id, deed_id, doer: UserRef, excerpt (first 80 chars, while the text is kept), removed_at}`. `POST /api/feed/me/comment-notices/{comment_id}/seen` sets `removal_notice_seen_at` (204, idempotent; someone else's comment → 404). A restored comment drops out of the list. The notice never names the reporter, the moderator or the note. `CommentModerationDecided` still lets M6 send a real notification.

| Status | code                                                  |
| ------ | ----------------------------------------------------- |
| 403    | `onboarding_required`, `conflict_of_interest`         |
| 404    | `comment_not_found`                                   |
| 409    | `case_not_open`, `not_restorable`                     |
| 422    | `invalid_reason`, `note_required`, `details_too_long` |
| 429    | `rate_limited`                                        |

### 3.4b Contract clarifications (from F9, 2026-11-19)

Settled by the main session so frontend and backend build to the same shapes:

1. **Notices:** `GET /api/feed/me/notices` returns a bare array of keys (`["public_feed_intro"]`); `GET /api/feed/me/comment-notices` returns a bare array of `CommentNotice = {comment_id, deed_id, doer: UserRef, excerpt|null, removed_at}`. Both are small and unpaginated.
2. **Comment report:** `POST /api/feed/comments/{id}/reports` returns `202 {status: "received"}`, identical for a new report and a duplicate (same as photo reports).
3. **Photo-report `case` object (#11, #12):** `{case_id, status, priority, top_reason, report_count, opened_at, resolved_at|null, decision: "kept"|"removed"|null, removal_reason|null}`.
4. **Photo-report detail `photo` (#12)** also has `id` (needed for restore), `restorable_until|null` (= `removed_at + removed_photo_retention_days` while `admin_removed` and not purged) and `purged_at|null`.
5. **Comment-report detail (#15):** `{case, comment: {id, body|null, author: UserRef, created_at, state: "visible"|"removed"|"deleted", restorable_until|null}, deed: {doer, reporter, category, area_name, listed}, reports: [{reason, details, reporter: {kind: "member", handle}, created_at}], decisions: [{action, note, actor: UserRef, created_at}], author_removed_last_90_days}`; `case` has the same fields as in 3 (no `removal_reason`).
6. **Decision and restore responses:** photo decision, photo restore, comment decision and comment restore all return `200` with the case detail. Clients may ignore the body and refetch.
7. **New `GET /api/feed/options`** (CurrentUser): `{comment_max_chars, top_badge_ranks, top_periods: [{id: "week"|"month"|"year", days}], comment_report_reasons: [{id, label, priority}], report_details_max}`. `GET /api/media/options` gains `decision_note_max` (for the admin note box).
8. **Routes:** `/admin/comment-reports` and `/admin/comment-reports/:caseId` behind `RequireRole(['moderator','admin'])`.
9. **`RequireRole` when signed out:** redirect to `/login?next=…` (like `RequireAuth`); NotFound only for a signed-in user without the role.
10. **Category label (from F10):** `PostOut`, `PostDetail` and `PublicDeed` also carry `category_label` (the display label from the deeds category options), because not-onboarded users and visitors can't call `GET /api/deeds/options`.
11. **Share recording:** `POST …/shares` stays for onboarded members; a visitor's or not-onboarded user's share isn't recorded (analytics only, M8).
12. **Upload PUT:** browsers can't set `Content-Length`; the client checks the file size equals the signed length before the PUT, and the store still enforces the signed length.

### 3.5 Who may do what (summary)

| Action                      | Visitor          | Member               | Doer                                 | Reporter (uploader)        | Moderator or admin (Q34)                              |
| --------------------------- | ---------------- | -------------------- | ------------------------------------ | -------------------------- | ----------------------------------------------------- |
| See a listed deed           | public page only | ✓                    | ✓                                    | ✓                          | ✓                                                     |
| See a hidden deed           | –                | –                    | ✓                                    | ✓                          | admin page only                                       |
| Like, save, comment, follow | –                | ✓                    | ✓                                    | ✓                          | ✓                                                     |
| Delete a comment            | –                | own (erased at once) | any on the deed                      | any on the deed            | remove through a report, restore within 30 days (Q38) |
| Report a comment            | –                | ✓ (not own)          | ✓                                    | ✓                          | –                                                     |
| Hide / show                 | –                | –                    | own hide                             | own hide                   | –                                                     |
| Add photos                  | –                | –                    | –                                    | within 24 h                | –                                                     |
| Remove a photo              | –                | –                    | any photo on their deed, final (Q29) | own photos, any time (Q30) | remove / restore with note (not doer-removed photos)  |
| Report a photo              | ✓ (IP-limited)   | ✓                    | ✓                                    | – (own)                    | –                                                     |
| See report outcome          | –                | –                    | –                                    | ✓ (resolved only)          | ✓                                                     |

### 3.6 Kind-words filter (`feed/_kindness.py`)

**Languages (PO decision Q35, 2026-11-19): English only in M3, built so other languages are switched on by config.** Nothing outside `_kindness.py` and the `feed` config knows which languages exist; no code path names English.

- `comment_filter_languages` holds one **language pack** per language code: its own `blocked_terms`, its own `substitutions` (look-alike characters), the combining marks its script needs kept (`keep_marks`, e.g. Devanagari and Kannada vowel signs later), and `squashed_min_letters`. Packs are independent: a term or rule in one never affects another.
- `comment_filter_enabled_languages` lists the packs in force (M3: `["en"]`). Validation: every enabled code must have a pack, codes are ISO 639-1, no extras. A comment is checked against **every** enabled pack (people mix languages; there is no language detection).
- Adding Hindi or Kannada later = write the pack (native speakers chosen by the PO) and publish a new `feed` config version with the code added to the enabled list. Tokenising and normalising are already script-agnostic (Unicode letters of any script), so no code change is expected; a script needing a special rule gets a small, separately tested hook keyed by language code.

A pure function `check_comment(text, cfg) -> None | Literal["not_kind","link","phone"]`. For each enabled pack:

1. Normalise: Unicode NFKC, casefold, remove zero-width characters and combining marks used for evasion (except the pack's `keep_marks`), apply the pack's `substitutions` (English: `0→o, 1→i, 3→e, 4→a, 5→s, 7→t, @→a, $→s`), collapse any letter repeated 3+ times to 2.
2. Tokenise on anything that isn't a letter (any script); match whole tokens and multi-word phrases against the pack's `blocked_terms` (normalised the same way at load). Also match the text with spaces and punctuation removed against the pack's terms of `squashed_min_letters`+ letters, to catch "s t u p i d" without Scunthorpe false positives on short words.
3. Links: `https?://`, `www.`, or a bare domain with a known TLD → `link`. Phone numbers: 10+ digits after removing spaces, dashes and `+91` → `phone`.
4. The error never says which word matched (canvas copy: "Please keep it kind. Your comment includes a word we don't allow. Try rewording it.").

M3 ships the `en` pack only: a short English starter list from the architect, reviewed by the PO before launch, kept up to date through config. Steps 3 and 4 (links, phone numbers, the friendly message) are language-independent and unchanged. Comment deletion by the author, doer and reporter is unchanged.

---

## 4. Photo pipeline and storage

### 4.1 Flow

```
Browser                          API (media)                  Storage (GCS / SeaweedFS)        Worker
pick file → resize to ≤2048 px
POST /uploads {type,size} ──────► row pending_upload, sign PUT
            ◄─── {photo_id, url}
PUT bytes (signed type+length) ─────────────────────────────► staging/{id}
POST /uploads/{id}/complete ────► HEAD + first 16 bytes ─────► ok → uploaded
(user may blur in the browser; regions only)
POST /api/deeds  (deed created)
POST /media/deeds/{id}/photos ──► attach, regions, queued
                                                                                    claim queued (SKIP LOCKED)
                                                                ◄── GET staging ──── decode, validate, transpose,
                                                                                    sRGB, blur, resize, WebP
                                                                ◄── PUT photos/{id}/thumb, display
                                                                                    ready; DELETE staging
GET /api/feed ──────────────────► signed GET URLs for ready photos
<img src=signed URL> ───────────────────────────────────────► private object
```

### 4.2 `app/core/storage.py` (provider-neutral)

```python
class ObjectStorage(Protocol):
    def presign_put(self, key: str, content_type: str, content_length: int, expires_s: int) -> PresignedRequest
    def presign_get(self, key: str, *, signed_at: datetime, expires_s: int) -> str
    async def head(self, key: str) -> ObjectInfo | None          # size, content_type
    async def get_range(self, key: str, start: int, end: int) -> bytes
    async def get_bytes(self, key: str, max_bytes: int) -> bytes   # refuses larger objects
    async def put_bytes(self, key: str, data: bytes, content_type: str, cache_control: str) -> None
    async def delete(self, key: str) -> None                      # one key; idempotent
    async def list_older_than(self, prefix: str, before: datetime) -> AsyncIterator[str]
def get_storage() -> ObjectStorage   # FastAPI dependency; tests override with MemoryStorage
```

- **Implementation `S3Storage`** on boto3 (new dependency; network calls via `asyncio.to_thread`). Two clients: one with the **internal** endpoint for server calls, one with the **public** endpoint used only for signing (signing is offline, so the API can sign URLs for a host it can't reach; in docker the API talks to `http://storage:8333` while browsers use `http://localhost:8333`).
- `botocore.config.Config(signature_version="s3v4", s3={"addressing_style": settings.storage_addressing_style}, request_checksum_calculation="when_required", response_checksum_validation="when_required")`. The two checksum settings are **required for GCS**: recent boto3 versions add CRC checksum headers by default, which GCS's XML API doesn't accept.
- **No multi-object delete:** GCS's XML API doesn't support S3 `DeleteObjects`, so `delete` is one request per key and cleanup loops.
- **No bucket config through S3 calls:** CORS and lifecycle rules on GCS are set with `gcloud storage buckets update` (infrastructure, before launch); locally the init container sets CORS through the S3 API.
- **Stable signed GET URLs (D34):** `presign_get` signs with `X-Amz-Date = signed_at`, where the caller passes `floor(now, url_step_minutes)`, and `X-Amz-Expires = url_ttl_minutes × 60`. Implement with botocore's SigV4 query signer and an injected timestamp (a small subclass), and test it against botocore's own output with frozen time. `urls_expire_at = signed_at + ttl` is returned so the frontend can refetch before expiry.
- **Objects** are written with `Cache-Control: private, max-age=3600` and keys that include a version suffix (`display-1.webp`), so an object never changes under the same key.
- **`MemoryStorage`** (tests): dict-backed; presign returns `memory://…` URLs; tests "upload" with `put_bytes`.

**Settings** (`app/core/config.py`, env `GD_STORAGE_*`; deployment settings, not limits): `storage_endpoint_url`, `storage_public_endpoint_url`, `storage_region` (`auto` for GCS, per Google's boto3 sample; `us-east-1` locally), `storage_bucket`, `storage_access_key_id`, `storage_secret_access_key`, `storage_addressing_style` (`path` locally, `path` or `virtual` on GCS). The production validator refuses non-`https` endpoints, the dev access key, and an empty secret.

**Production (GCS) setup**, done once before launch (task OPS1, needs a GCP project): bucket in `asia-south1`, uniform bucket-level access, public access prevention **enforced**, a dedicated service account with `roles/storage.objectAdmin` on this bucket only, an HMAC key for it (secret in the deployment's secret store), CORS allowing `PUT` and `GET` from the web origin with `Content-Type`, and a lifecycle rule deleting `staging/` objects after 3 days as a backstop.

### 4.3 Local storage: SeaweedFS

MinIO's community images are no longer published, and its repository was archived in 2026. Candidates for a single-node local S3:

| Option                                            | Licence    | Setup                                                           | Verdict                                                                                                     |
| ------------------------------------------------- | ---------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **SeaweedFS** (`chrislusf/seaweedfs`)             | Apache-2.0 | One container (`weed server -s3`) plus a small S3 identity JSON | **Chosen.** Mature, actively maintained, documents SigV4 presigned URLs, CORS and multipart; one container. |
| S3Proxy (`andrewgaul/s3proxy`)                    | Apache-2.0 | Very easy                                                       | Fallback. Fine for tests; less sure about presigned-URL and CORS behaviour from a browser.                  |
| Zenko CloudServer (`ghcr.io/scality/cloudserver`) | Apache-2.0 | Easy                                                            | Fallback; the Docker Hub image is outdated.                                                                 |
| Garage (`dxflrs/garage`)                          | AGPL-3.0   | Needs an init step (layout, key) and TOML                       | Too much setup for a dev stand-in.                                                                          |
| RustFS                                            | Apache-2.0 | Easy                                                            | Alpha; not yet.                                                                                             |

docker-compose gains (task B17):

```yaml
storage:
  image: chrislusf/seaweedfs:<pinned version>
  command:
    [
      "server",
      "-dir=/data",
      "-s3",
      "-s3.port=8333",
      "-s3.config=/etc/seaweedfs/s3.json",
    ]
  ports: ["8333:8333"]
  volumes:
    [storage-data:/data, ./infra/seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro]
storage-init: # creates the bucket and sets CORS for http://localhost:5173, then exits
  image: amazon/aws-cli:<pinned version>
  entrypoint:
    [
      "sh",
      "-c",
      "aws --endpoint-url http://storage:8333 s3api create-bucket --bucket gooddeeds-media || true; aws --endpoint-url http://storage:8333 s3api put-bucket-cors --bucket gooddeeds-media --cors-configuration file:///init/cors.json",
    ]
```

`api` and `worker` get `GD_STORAGE_ENDPOINT_URL=http://storage:8333`, `GD_STORAGE_PUBLIC_ENDPOINT_URL=http://localhost:8333`, dev keys from `s3.json`, bucket `gooddeeds-media`. **The first step of B17 is a spike:** from a browser on `localhost:5173`, PUT through a presigned URL and display through a presigned GET. If SeaweedFS's CORS handling fails that test, switch to the S3Proxy or CloudServer fallback, and record the outcome in CLAUDE.md. Where GCP credentials exist, the spike also runs the same calls against a GCS test bucket.

### 4.4 Validation layers and the worker

1. **Create:** declared type in the allowlist; declared size ≤ `upload_max_bytes`; per-user pending cap.
2. **Presigned PUT:** `Content-Type` and `Content-Length` are signed, so the store rejects anything else; URL valid for `upload_url_ttl_seconds`.
3. **Complete:** `HEAD` size equals the declared size; the first 16 bytes match the declared type.
4. **Worker (`media/_pipeline.py`, pure functions; `media/jobs.py`):**
   - A periodic job `media.process_queued` (every 5 s, `queueing_lock`) claims up to N rows: `UPDATE photos SET status='processing', processing_started_at=now(), attempts=attempts+1 WHERE id IN (SELECT id … WHERE status='queued' ORDER BY attached_at FOR UPDATE SKIP LOCKED LIMIT N) RETURNING …`, commits, then processes each outside a transaction. Rows stuck in `processing` longer than `processing_stuck_minutes` go back to `queued`; after `processing_max_attempts` they become `failed` (the owner sees "This photo couldn't be prepared. Remove it and try again.", _interim copy_).
   - `get_bytes(staging_key, max_bytes=upload_max_bytes)`.
   - Pillow (new dependency) with `Image.MAX_IMAGE_PIXELS = max_image_pixels` and `DecompressionBombError` treated as `too_many_pixels`; `Image.open`, then `verify()`, then reopen.
   - `img.format` must be JPEG, PNG or WEBP and match the declared type; animated images (`getattr(img, "n_frames", 1) > 1`) → `animated`; either side < `min_image_side_px` → `corrupt`.
   - `ImageOps.exif_transpose` (apply orientation, then the EXIF is gone with the re-encode).
   - Colour: convert an embedded ICC profile to sRGB (`ImageCms`), then drop it; flatten alpha onto white; mode RGB.
   - **Blur** (§4.5) on the full-resolution image.
   - Variants: for each config variant, `thumbnail((max_side, max_side), LANCZOS)` from the blurred image; save as **WebP** at `webp_quality` with **no** `exif`, `icc_profile` or `xmp` arguments, so the output carries no metadata at all (no GPS, camera, time or device). Upload with `put_bytes`.
   - Commit: `ready`, `variant_keys`, `width`, `height`, `processed_at`. Then delete the staging object and set `staging_deleted_at` (a failure leaves it for the cleanup job).
   - A bad file: `rejected` with `reject_reason`, staging deleted; the owner sees "This photo couldn't be used. Try a different one." _(interim copy)_.
   - CPU work runs in `asyncio.to_thread`. Procrastinate queue `media`, so it can get its own worker later; in M3 the one worker also listens to it.

5. **Browser side (defence in depth, not a control):** `accept="image/jpeg,image/png,image/webp"` (this makes iPhones convert HEIC to JPEG), and resizing to ≤ 2048 px with `createImageBitmap(file, {imageOrientation: 'from-image'})` and canvas `toBlob('image/jpeg', 0.85)` before upload. This also drops EXIF and saves mobile data; the server still strips everything itself.

### 4.5 Blur regions

- The client sends ellipses as `{x, y, w, h}`, fractions of the **displayed, orientation-corrected** image (top-left origin): `0 ≤ x, y`, `x + w ≤ 1`, `y + h ≤ 1`, `w, h ≥ blur_min_region`, at most `blur_max_regions`. The browser resize step already applies orientation, and the worker applies `exif_transpose` before blurring, so both use the same frame.
- Worker, per region, on the full-resolution image: take the bounding box expanded by 10 %; **pixelate** (downscale to blocks of `max(8 px, side / 8)`, upscale with NEAREST), then **Gaussian blur** with radius `max(w_px, h_px) / 6`; paste back through an elliptical mask with a soft edge. Pixelating first makes the result irreversible (a plain light Gaussian blur can be partly undone).
- Every variant is made from the blurred image; the unblurred pixels exist only in the staging object (deleted after processing) and in worker memory (D33).
- Stored: `blur_regions` (numbers only) for the admin "faces blurred" line and for tests.

### 4.6 Extension points for future privacy features

`_pipeline.py` runs an ordered list of steps over a `PhotoJob` (image, declared type, owner kind, regions, config): `decode → validate → orient → colour → [privacy steps] → derive → encode`. M3's only privacy step is `apply_blur_regions`. A later step (for example automatic face _suggestions_ shown in the blur editor, a guardian-consent hold for children, or a review hold) is one function added to the list and a config flag, without changing upload, attach, storage or serving. Video (deferred) gets its own pipeline behind the same `photos`-like row and the same signed serving.

### 4.7 Serving

- Only `media.service` creates GET URLs: `public_photos(deed_ids)` (ready, `takedown='none'`, deed photos only) for the feed and public page; owner views for My deeds and self-logs; admin views for the review page. Signing per D34.
- **Doer removal and uploader deletion (Q29):** the objects are deleted right after commit, so any URL already handed out (feed, deed page, public page, a copied link) returns 404 from storage immediately; no API lists the photo again, the public page is `no-store`, and the app drops the photo from every cached query at once. What can't be recalled is a copy already on someone's device (a browser that loaded it before removal, a screenshot); the app never shows it again.
- **Admin removal (PO decision 2026-11-19):** the variants move to `held/{id}/…` right after the decision commits, so every URL already issued returns 404 at once; a restore copies them back under a new version, so links from before the removal never work again. Only the admin review endpoint signs `held/` keys. Until a move finishes, `public_photos` already excludes the photo (it checks `takedown`), and `media.move_objects` retries a failed move every minute.
- Exposure after **hide or withdraw** (the photo itself isn't removed) is still bounded by the URL lifetime (≤ 60 min, config); lowering `url_ttl_minutes` shortens it at the cost of browser caching.
- The frontend refetches the query when `urls_expire_at` is within 5 minutes, and on an image load error.
- Later, if egress grows: Cloud CDN with a backend bucket and Cloud CDN signed URLs or cookies, issued by the same `media.service` (one function changes). Not needed for launch.
- CSP: `img-src` must allow the storage origin (`https://storage.googleapis.com` in production).

### 4.8 Cleanup jobs (`media/jobs.py`)

| Job                           | Schedule                                                                                                | Work                                                                                                                                                                                                               |
| ----------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `media.expire_uploads`        | every 30 min                                                                                            | `pending_upload`/`uploaded` rows older than `pending_upload_ttl_hours` → delete staging object, `expired`                                                                                                          |
| `media.purge_detached`        | hourly                                                                                                  | `self_log` photos whose `self_log_id` became NULL (self-log deleted) → delete objects, `deleted`                                                                                                                   |
| `media.purge_deleted`         | every 5 min                                                                                             | `deleted` rows with `objects_purged_at IS NULL` → delete variants and staging, set `objects_purged_at` (retries failures)                                                                                          |
| `media.move_objects`          | every 1 min                                                                                             | rows with `object_move` set → finish the hold or release copy/delete, then clear it (idempotent)                                                                                                                   |
| `media.purge_removed`         | nightly                                                                                                 | `admin_removed` photos with `removed_at` older than `removed_photo_retention_days` → delete the `held/` variants, `objects_purged_at`; restore then returns 409 `not_restorable` (Q32)                             |
| `media.sweep_staging`         | nightly                                                                                                 | list `staging/` objects older than 48 h with no row in `pending_upload`/`uploaded`/`queued`/`processing` → delete (catches PUTs that arrived after expiry)                                                         |
| `feed.erase_deleted_comments` | nightly                                                                                                 | set `body = NULL` on comments deleted by the doer, the reporter or moderation more than `deleted_comment_retention_days` (30) ago (author deletions are already erased); restore then returns 409 `not_restorable` |
| `feed.refresh_top`            | per period, every `top_week_refresh_minutes` / `top_month_refresh_minutes` / `top_year_refresh_minutes` | new Top snapshot for that period; keep the newest two per period                                                                                                                                                   |

All batch in short transactions until a batch comes back short, like M2's jobs.

---

## 5. Events

**Consumed by `feed` (`feed/handlers.py`), all through one idempotent `sync_post(deed_id)`:**

| Event                                                                               | Effect                                                                                                                     |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `DeedVerified`                                                                      | upsert `feed_posts` with `state='published'`, `published_at = verified_at`, doer and reporter                              |
| `DeedWithdrawn`                                                                     | `state = 'withdrawn'` (if a row exists; a withdrawn pending deed has none)                                                 |
| `DeedRejected`                                                                      | `state = 'rejected'` (a later `removed` status from M7 maps to `removed`)                                                  |
| `DeedSubmitted`, `DeedEdited`, `DeedPrizeRankChanged`, `DeedCountsForPointsChanged` | **not subscribed**: pending deeds aren't published, content is read live (D38), and scoring fields must not reach the feed |

`sync_post` reads `deeds.service.get_deed` and derives the state from the deed's current status, so handler order and retries don't matter, and a terminal state is never overwritten by `published`. M5 adds a `FlagRaised` handler that sets `held`.

**Emitted** (payloads: IDs and enums only, no text, no coordinates):

| Event                      | `event_type`                      | Emitted by / when                             | Payload                                                           | Subscribers                                                     |
| -------------------------- | --------------------------------- | --------------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------- |
| `PhotoModerationDecided`   | `media.photo_moderation_decided`  | admin keep / remove / restore                 | `photo_id, case_id, deed_id, uploader_id, action, removal_reason` | none in M3; M6 notifications                                    |
| `CommentModerationDecided` | `feed.comment_moderation_decided` | moderator/admin keep / remove / restore (Q38) | `comment_id, case_id, deed_id, author_id, action`                 | none in M3; M6 notifications                                    |
| `PhotoRemovedByDoer`       | `media.photo_removed_by_doer`     | the doer removes a photo (Q29)                | `photo_id, deed_id, uploader_id, doer_id`                         | none in M3 (the uploader sees it in My deeds); M6 notifications |
| `DeedLiked`                | `feed.deed_liked`                 | a new like row                                | `deed_id, user_id`                                                | none in M3; M6, M8                                              |
| `CommentPosted`            | `feed.comment_posted`             | a new comment                                 | `deed_id, comment_id, author_id`                                  | none in M3; M6                                                  |
| `UserFollowed`             | `feed.user_followed`              | a new follow row                              | `follower_id, followed_id`                                        | none in M3; M6                                                  |

Unlikes, unfollows, removed followers, hides and saves emit nothing (hides are audit-logged; saves are private).

---

## 6. UI flows (frontend-developer)

Visual design: the approved canvases `docs/design/f7/` (Feed) and `docs/design/m3-photos/` (Photos and photo reports). Each has a mobile prototype (`Main.dc.html`, press Play; the photos prototype switches between Priya, Rahul and Admin) and key screens at 390 px and 1440 px. Copy, spacing and colours follow the canvases; copy quoted here is from them unless marked _interim_. Sample people: Priya Sharma (viewer/reporter), Ahmed Khan (doer), Rahul Verma (viewer).

### 6.1 Files

| Area    | Files                                                                                                                                                                                                                                                                                                    |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| API     | `src/features/feed/feedApi.ts`, `feedQueries.ts`; `src/features/media/mediaApi.ts`; `src/features/admin/adminApi.ts`. Uploads to storage use plain `fetch` with **only** the signed headers: never `src/lib/api.ts`, so the access token and `X-Device-Id` never go to the storage host                  |
| Feed    | `src/features/feed/`: `FeedPage` (tabs), `PostCard`, `PhotoCarousel`, `FeedSkeleton`, `ShareSheet`, `PostMenuSheet`, `DeedPage` (`/deeds/:id`), `CommentsSection`, `VisibilityControl`, `SavedPage`, `ConnectionsPage`, `PublicDeedPage`, `useSignedUrlRefresh`                                          |
| Media   | `src/features/media/`: `resizeImage.ts`, `usePhotoUploads.ts` (create → PUT with progress → complete; retry; discard), `PhotoPicker` (thumbnails + Add), `BlurEditor`, `ReportPhotoSheet`, `OwnerPhotos` (My deeds and self-log), `PhotoOutcomeBanner`                                                   |
| Admin   | `src/features/admin/`: `AdminLayout` (dark header "ADMIN", nav "Photo reports" · "Comment reports"), `PhotoReportsPage`, `PhotoReportReviewPage`, `CommentReportsPage`, `CommentReportReviewPage` (shared queue/review components, Q38), `RemovePhotoDialog`                                             |
| Shared  | `src/app/guards.tsx`: `RequireRole(['moderator', 'admin'])` for the photo-report routes (Q34); `src/app/routes.tsx`; `src/features/home/HomePage.tsx` (signed out: welcome, as now; signed in: `FeedPage`)                                                                                               |
| Changed | `features/report/DeedForm.tsx` (photos section, then the attach call after send), `ReportResult.tsx`, `features/selflog/SelfLogFormPage.tsx`, `features/deeds/DeedDetailPage.tsx` and `DeedCard.tsx` (photos, visibility chip, outcomes), `features/profile/ProfilePage.tsx` and `PublicProfilePage.tsx` |

No new npm dependency (carousel, sheets and the blur editor are small components; pointer events for dragging).

### 6.2 Routes

| Route                          | Guard         | Page                                                                                                                                                                                           |
| ------------------------------ | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| `/`                            | none          | signed out: welcome (as now); signed in: FeedPage, `?tab=nearby` (default) \| `following` \| `top` (+ `&period=week` (default) \| `month` \| `year`, so back and shared links keep the period) |
| `/deeds/:id`                   | RequireAuth   | DeedPage (comments, hide/show)                                                                                                                                                                 |
| `/d/:id`                       | none          | PublicDeedPage; a signed-in visitor is redirected to `/deeds/:id`                                                                                                                              |
| `/u/:handle`                   | none (as now) | PublicProfilePage + follow, counts, deeds (signed out: counts only plus "Join to follow", interim)                                                                                             |
| `/me/saved`                    | RequireAuth   | SavedPage                                                                                                                                                                                      |
| `/me/connections`              | RequireAuth   | ConnectionsPage, `?tab=followers                                                                                                                                                               | following` |
| `/community-guidelines`        | none          | short static page; we draft the copy, the PO approves it before launch (comment-removed notice)                                                                                                |
| `/photo-guidelines`            | none          | short static page; we draft the copy, the PO approves it before launch (Q39)                                                                                                                   |
| `/admin/photo-reports`         | RequireRole   | PhotoReportsPage, `?status=open                                                                                                                                                                | resolved`  |
| `/admin/photo-reports/:caseId` | RequireRole   | PhotoReportReviewPage                                                                                                                                                                          |

`/me/deeds/:id` stays the private owner view (M2 §5.6).

### 6.3 Screens and states

**Home feed (F7 §1)**

- Tabs **Nearby · Following · Top** (segmented, `role="tablist"`), intro line under them: "Good deeds verified around Bangalore, newest first." / "Deeds by people you follow." / Top: "The most appreciated deeds published in the last 7 days." (30 days / 365 days for the other periods).
- **Top period dropdown** (Q26), shown only on the Top tab, under the tabs: a native `<select>` labelled "Period" with **Top this week (last 7 days)** · **Top this month (last 30 days)** · **Top this year (last 365 days)**; default week. Changing it updates `?period=` (replace, not a new history entry), reloads the list from rank 1 and moves focus to the list heading; it never adds a tab.
- **Post card:** avatar initials (tap → `/u/:handle`), **doer name**, "recognised by {reporter}" ("recognised by you" when it's mine); Follow / Following pill for the doer (hidden on my own deeds); line "{category} · {area} · {time}"; green chip "Verified by a witness"; on Top, an amber "#{n} this week" / "#{n} this month" / "#{n} this year" chip (matching the selected period) for ranks ≤ `top_badge_ranks`; photo carousel (260 px mobile / 360 px desktop, "1 / n" counter, dots; swipe and arrow keys); description; actions: like (heart + count, `aria-pressed`), comments (count → `/deeds/:id`), Share, save (bookmark, `aria-pressed`, label "Save" / "Saved. Tap to unsave"). "People helped" only on the deed page. A "⋯" menu (photos canvas) opens PostMenuSheet: **Report photo** (only when the card has photos and I'm not the uploader) and **Copy link**.
- Likes, saves and follows are optimistic and roll back on error.
- **Loading:** two skeleton cards. **End:** "You're all caught up" / "Saw something good today? Recognise the person while you're both there." [Record a deed]. More pages load on scroll (IntersectionObserver), with a "Show more" button as fallback.
- **Following, empty:** "You're not following anyone yet" / "Follow people from their deeds or profiles to see their good deeds here." [Browse Nearby].
- **Nearby, empty** (_interim_): "No deeds yet" / "Be the first: record a good deed you see today." [Record a deed].
- **Not onboarded** (D44, Q42 = A, _interim_ copy): the feed, deed pages and profiles are readable; tapping like, save, comment, follow or report comment shows "Finish setting up to like, comment and follow." [Finish setting up] → `/onboarding`.
- **Public-feed notice** (M2 Q21 asked to repeat it at M3 launch; PO decision Q41 = A, 2026-11-19): a card above the feed, once per person (not per device): "Deeds people record about you now appear in the public feed with your name and the area. You can hide any of them from the deed's page." [See my deeds] [OK]. Shown to every signed-in user the first time they open the feed (existing users at M3 launch, and new users later). Either button marks it seen on the server: `GET /api/feed/me/notices` returns the unseen notice keys (`["public_feed_intro"]`), `POST /api/feed/me/notices/{key}/seen` records it (204, idempotent; unknown key 404). Stored in `feed_notices_seen` (`user_id`, `notice_key`, `seen_at`; PK `(user_id, notice_key)`), reusable for later one-time notices.

**Share sheet:** "Share this deed" / "{doer} · {category}"; **WhatsApp** (`https://wa.me/?text=` + encoded text and link), **Copy link** (clipboard; toast "Link copied", _interim_), **More options (Instagram and other apps)** (`navigator.share({title, text, url})`; the row is hidden when `navigator.share` is missing); note "Anyone with the link can see this one deed, but not your profile or other deeds. If the deed is hidden, the link stops working."; [Cancel]. Each choice calls `POST …/shares`. Share text (Q40, generic so no name or area is left in other apps): "A good deed was shared with you on Good Deeds. {url}" _interim_; people can add their own words before sending.

**Deed page `/deeds/:id` (F7 §2)**

- Back "Feed". The card as above, then "Helped {n} people · Checked by QR code and location" ("Helped 1 person"; the count part hidden when null).
- **Visibility row** (doer or reporter only): "You reported this deed. It's in the public feed." / for the doer _interim_: "This deed is about you. It's in the public feed." [Hide from feed].
- **Hide dialog:** "Hide this deed from the feed?" / "It stays verified and still counts on Ahmed's profile and for points. Its share link stops working. You can show it again at any time." [Hide from feed] [Cancel]. (Q24 = A: each person's hide is their own.)
- **Hidden banner:** "Hidden from the feed" / "Only you and Ahmed can see it. It's still verified and still counts on Ahmed's profile. Its share link is turned off." [Show in feed]. If the other person hid it (Q24 = A): "{name} hid this deed from the feed. Only you and {name} can see it. …" without the button (_interim_).
- **Comments ({n})**: oldest first; each: avatar, **name** · relative time, text, **Delete** only when `can_delete`. Note under the list for doer/reporter: "You see Delete on every comment because you reported this deed. Ahmed sees it too." (doer variant, _interim_: "You see Delete on every comment because this deed is about you.").
- Composer: avatar, textarea "Add a kind comment…", `maxLength` = `comment_max_chars`, [Post]; help "Be kind. Comments are public."; counter "{len} / 300".
- Errors inline under the composer: `comment_not_kind` → "Please keep it kind. Your comment includes a word we don't allow. Try rewording it." (red outline); `rate_limited` → "You're commenting very fast — try again in a minute"; `comment_has_link` → "Links aren't allowed in comments." _interim_; `comment_has_phone` → "Please leave out phone numbers." _interim_.
- **Report comment (Q38):** a "⋯" on each comment that isn't mine opens a sheet _interim_: "Report this comment" with reasons "Threatening or harassing", "Hateful", "Shares someone's personal information", "Spam or scam", "Something else", optional details, [Send report]; then "Thanks. Our team will review it. The comment stays up until they decide." Shown to doer and reporter too (alongside Delete).
- **Comment-removed notice (PO decision 2026-11-19):** a one-time card at the top of the feed for the author _interim_: "Your comment on {doer}'s deed was removed" / "It broke our community guidelines. \"{excerpt}…\"" [Read the community guidelines] [OK]. One card per removed comment, oldest first; [OK] marks it seen; it also expires after 30 days.
- **Delete dialog:** "Delete this comment?" / "It's removed for everyone. As the reporter of this deed, you can delete any comment on it." (author's own: "It's removed for everyone." _interim_) [Delete comment] [Cancel].
- 404 → "This deed isn't available" / "It may have been hidden or removed by the people involved." [Back to the feed] (_interim_ button).

**Public page `/d/:id` (F7 §3, photos canvas)**

- **Link preview and indexing (PO decision Q40, 2026-11-19):** the HTML served for `/d/*` carries only generic Open Graph tags (`og:title` "A good deed was shared with you", `og:description` "See it on Good Deeds", `og:image` the static app logo, `og:site_name` "Good Deeds"), the same for every deed; no doer name, photo URL, description, area or category is ever in the HTML head (crawlers don't run JavaScript, so the deed is only loaded by the app after the visibility check). `<meta name="robots" content="noindex, nofollow">` in that HTML, and `X-Robots-Tag: noindex, nofollow` on `/d/*` responses and on `/api/public/*`. `robots.txt` doesn't block `/d/` (a blocked page can't be seen to be `noindex`).
- Signed-out header: logo, Sign in, Join.
- "Someone shared this good deed with you." (blue note), then the card without action buttons, with "{n} likes · {n} comments", a "⋯" menu with Report photo and Copy link.
- "See more good deeds like this" / "Join Good Deeds to like, comment, follow Ahmed and explore other people's deeds." [Join Good Deeds] [Sign in]; footnote "Good Deeds only shows deeds confirmed in person by someone who saw them. Exact locations and contact details are never shown."
- Tapping a name, like or comment goes to sign-up with `next=/deeds/:id`.
- **Unavailable:** "This deed isn't available" / "It may have been hidden or removed by the people involved." [Discover Good Deeds] → `/`.
- A removed photo is simply absent (Q33 = A), whoever removed it (doer, uploader or admin): no placeholder, gap, "removed" label or changed count anywhere viewers look (feed card, `/deeds/:id`, public page, profile lists). The carousel counter and dots count only the remaining photos ("1 / 2"); with none left the card shows as a text-only deed. Only the uploader is told, in My deeds (Q29 notice or review outcome).

**Report a photo (photos canvas §2)**: sheet "Report this photo", legend "Why are you reporting it?", radios: "I'm in this photo and didn't agree" · "A child is shown without a parent's consent" · "It shows private details (like a phone number or address)" · "It's inappropriate or harmful" · "Something else"; "Add details (optional)" textarea ("Anything that helps our team decide"); note "Our team reviews every report. The photo stays up until they decide. The person who posted it won't see who reported it."; [Send report] (disabled until a reason is picked) [Cancel]. Sent: "Thanks for telling us" / "Our team will review this photo. Reports about children and privacy are looked at first." [Done]. The photo reported is the one showing in the carousel. Rate limit → "Too many reports from this connection. Please try again later." _interim_.

**Profiles and follows (F7 §3)**

- **Other profile `/u/:handle`:** avatar, name, "@handle" (C1), Follow / Following button (`aria-pressed`), counts grid **Verified Deeds · Self-log Deeds · Followers · Following** (2 × 2 mobile, 4 across desktop), heading "{first name}'s deeds", their listed deeds as cards.
- **Own profile:** under the counts row, a line "{n} followers · {n} following · Saved" linking to `/me/connections` and `/me/saved` (room left in M2 §5.6).
- **Connections `/me/connections`:** title = my name, tabs "Followers · {n}" / "Following · {n}"; rows with avatar, name, @handle and **Remove** (followers) or Follow/Following (following); note "Removing a follower is quiet: they aren't told, and they can follow you again later."
- **Saved `/me/saved`:** "Saved", a lock icon and "Only you can see what you save.", cards. Empty (_interim_): "Nothing saved yet" / "Tap the bookmark on a deed to keep it here."

**Photos on the deed form (photos canvas §1)**

- Rendered only when `media options.photos_enabled`. Section "Photos (optional)" with "{n} of {photos_per_owner}".
- Thumbnails 76 px (96 px desktop), 4 in a row, each with a remove ✕ and, under it, **Blur face** / **Edit blur**; a "Blurred" chip on blurred ones; a dashed **Add** tile (opens the file picker; on phones this offers camera or library) until the limit.
- Help: "Good deeds are meant to be shared proudly. If someone would rather not be shown, blur their face or remove the photo."
- Upload state per thumbnail (_interim_, not on the canvas): a progress ring while uploading; on failure a red outline and "Retry"; type or size refused → "That file can't be used. Choose a JPG, PNG or WebP photo under 10 MB." (size from options).
- **Send:** if uploads are still running, the button reads "Uploading photos…" and waits. After `POST /api/deeds` succeeds, the form calls the attach endpoint with the regions. If attach fails, the result screen adds: "Your deed was sent, but the photos didn't upload. You can add them from My deeds until {time}." _interim_.
- **Result** (C5): the M2 result plus, when photos were attached, "Your photos will appear in a few moments." _interim_.

**Blur editor (photos canvas)**: full screen; "Blur a face (optional)"; "Drag the circle over the face of anyone who'd rather not be shown. Everyone else stays exactly as they are."; the photo (local object URL, never uploaded unblurred anywhere but staging) with circles drawn over it as a live preview (CSS blur in a clipped overlay); [Add a blur] adds a circle in the centre, [Remove this blur] removes the selected one; circles drag with pointer events, resize with a corner handle, and with arrow keys (move) and +/− (size) when focused (keyboard support and multiple circles go beyond the canvas, which shows one, _interim_); note "Only the blurred version is posted. You can change this until you send the report."; [Cancel] [Done]. Regions are converted to fractions of the displayed image (§4.5).

**Self-log form:** same photo section without Blur, help _interim_: "Only you can see these photos."

**My deeds detail (owner, `/me/deeds/:id`)**

- Photo grid (canvas: up to 3 across, 110 px high), a "Shown in feed" / "Hidden from feed" chip.
- Reporter: a ✕ on each of their photos **at any time** (Q30); [Add photos] only inside the window, with the line "You can add photos until {time}. You can remove your photos at any time." _interim_; after the window: "You can remove your photos at any time." _interim_. Remove confirm _interim_: "Remove this photo?" / "It's deleted for everyone." [Remove photo] [Keep it].
- **Doer (Q29):** on a verified deed about me, a ✕ on every photo (in My deeds detail and on `/deeds/:id`), whoever uploaded it. Confirm _interim_: "Remove this photo?" / "It will be removed from the feed, this deed and its share link. {reporter} can't put it back. Your deed stays verified and your points don't change." [Remove photo] [Keep it]. After removal the photo disappears from every list in the app at once.
- Processing placeholder "Preparing photo…" _interim_; failed and rejected copy in §4.4.
- **Outcome banners** (photos canvas §3), for `report_outcome_display_days`:
  - removed: "A photo was removed after review" / "Our team removed 1 photo because {reason text}. Your deed is still verified, and Ahmed's count and points haven't changed." [Read the photo guidelines]. Reason text: not*consented "someone shown in it hadn't agreed to appear"; child "it showed a child without a parent's consent"; private_details "it showed private details"; inappropriate / other "it didn't follow our photo guidelines" (\_interim* for the last three).
  - removed by the doer (Q29, reporter only): "{doer} removed 1 photo from this deed." ("{n} photos" when more), neutral, no reason and no button. _interim_
  - kept: "Review complete: your photos stay" / "Someone reported a photo on this deed. Our team reviewed it and it stays up. No action needed."
- The deed card in My deeds shows a small dot when an outcome is unread (_interim_; local storage per device).
- Withdrawn detail gets M2's deferred clause: "You withdrew this report on {date}. It no longer counts for {doer} and it has left the public feed."

**Admin (photos canvas §3, desktop first; works at 390 px)**

- **Queue:** "Photo reports"; tabs "Open · {n}" / "Resolved · {n}"; note "Children first, then privacy and harmful images, then the rest; oldest first within each. Photos stay visible until you decide." _interim_; rows: thumbnail, priority chip (**Child** red, **Privacy/Harm** orange, **Other** grey; Q44), the top reason, "{doer} · {category} · {area}", "{n} reports · oldest {age}", [Review].
- **Review:** "Review photo report"; the photo with a status chip ("Visible · report open", "Removed", "Kept after review"); "Photo {i} of {n} · Uploaded {date, time}"; **Deed** "{doer}, recognised by {reporter} (uploaded the photos)", "{category} · {area} · Verified · Shown in feed|Hidden from feed"; **Upload record** "Uploaded by {name} on {date, time}." and "No faces were blurred on this photo." / "{n} faces were blurred on this photo." _interim_; **Reports ({n})**: chip, reason, quoted details, "Visitor on a shared link · {age}" or "Member @{handle} · {age}"; **Decide**: "Note for the record (required)" ("Why you decided this"), help "{uploader first name} is told the outcome. The deed, its verification and points never change.", [Keep photo] [Remove photo]; a removal also picks the reason shown to the uploader (select, defaulting to the top report's reason; _interim_, not on the canvas).
- **Remove dialog:** "Remove this photo?" / "It disappears from the feed, the deed page and shared links. Priya is told it was removed after review. The deed, its verification and points don't change. You can restore it later." [Remove photo] [Cancel].
- **Done banner:** "Photo removed." / "Photo kept." + "Priya has been told the outcome. Your note and decision are in the audit log." [Restore photo] when removed (restore asks for a note, _interim_), with "Can be restored until {removed*at + 90 days}." \_interim*; after that date the button is gone and the page says "Deleted for good on {date}." _interim_ (Q32).
- `conflict_of_interest` → "Another admin needs to review this one: you're part of this deed." _interim_.
- **Comment reports (Q38)** _interim_, same layout as photo reports: queue "Comment reports" with tabs "Open · {n}" / "Resolved · {n}", rows with a priority chip, the top reason, an 80-character excerpt, "{author} on {doer}'s deed", "{n} reports · oldest {age}"; review shows the full comment, author, deed, reports, "{author} has had {n} comments removed in the last 90 days", note box, [Keep] [Remove]; done banner with [Restore comment] while restorable ("Can be restored until {date}.").
- A user without the role who opens `/admin/...` gets the NotFound page.

### 6.4 Frontend rules

- Never render coordinates, phone numbers or the doer of a pending deed (pending deeds aren't in the feed at all).
- Never send `Authorization` or `X-Device-Id` to the storage host.
- Refetch a list or page when `urls_expire_at` is less than 5 minutes away or an image fails to load.
- Every screen at 390 px and 1440 px; touch targets ≥ 44 px; sheets and dialogs `role="dialog" aria-modal="true"`, close on Esc, return focus; carousel and blur editor usable by keyboard; photos have alt text ("Photo {i} of {n}").
- Query keys: `['feed', tab]`, `['feed','deed',id]`, `['feed','comments',id]`, `['social',handle]`, `['media','deed',id]`, `['admin','photo-reports',status]`. Invalidate the deed and the lists after like, save, comment, hide/show.

---

## 7. Task list

Agents: **DB** database-specialist, **BE** backend-developer, **FE** frontend-developer, **QA** qa-engineer, **SEC** security-reviewer, **AD** architecture-diagram. All PO answers are in (Revision 2); no task is waiting.

| ID    | Owner                       | Task                                                                                                                                                                                                                                                                                                                                                                     | Depends on                              |
| ----- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------- |
| B17   | BE                          | **Storage spike, then core storage:** SeaweedFS + init container in docker-compose; browser presigned PUT/GET test from `localhost:5173` (fallback per §4.3); `app/core/storage.py` (`ObjectStorage`, `S3Storage` with the GCS settings, stable-URL signer, `MemoryStorage`); `GD_STORAGE_*` settings and production validator; `boto3` dependency; CLAUDE.md note draft | –                                       |
| DATA1 | DB                          | Ward dataset (Q28): find the latest official Greater Bengaluru ward boundaries, verify publisher, date and licence, write `data/AREAS_SOURCE.md`, build the simplified-name table for PO review, convert to `bangalore_areas.geojson`; add attribution to `/terms` if the licence needs it. Blocks DB4                                                                   | –                                       |
| DB4   | DB                          | Migration 0013: `areas` + seed from the ward dataset (Q28; after DATA1), `deeds.area_id` + backfill, `deeds` v5 (§2.1)                                                                                                                                                                                                                                                   | –                                       |
| DB5   | DB                          | Migration 0014: media tables, constraints, indexes, `photo_decisions` append-only trigger, `media` v1 seed (§2.2, §2.5)                                                                                                                                                                                                                                                  | –                                       |
| DB6   | DB                          | Migration 0015: feed tables (generated `listed` column), indexes, `feed` v1 seed, `feed_posts` backfill (§2.3, §2.5)                                                                                                                                                                                                                                                     | DB4                                     |
| B18   | BE                          | Deeds: `_areas.py` lookup at create/accept, `DeedView.area_name`, `get_deeds`, `self_log_owner`, `area_name` on `DeedOut`/`ScanOut`, options drops `photos_enabled`                                                                                                                                                                                                      | DB4                                     |
| B19   | BE                          | Media uploads and owners: options, create/complete/discard, attach to deed and self-log (lock, limits, window), owner reads with outcomes, delete (doer removal per Q29; uploader removal any time per Q30)                                                                                                                                                              | B17, DB5, B18                           |
| B20   | BE                          | Media pipeline and worker: `_pipeline.py` steps, blur, variants, claim job, retries, staging deletion; `pillow` dependency; `media.service.public_photos` / owner / admin URL builders                                                                                                                                                                                   | B17, DB5                                |
| B21   | BE                          | Media cleanup jobs (§4.8)                                                                                                                                                                                                                                                                                                                                                | B19, B20                                |
| B22   | BE                          | Photo reports and admin: report endpoint (feed visibility via `feed.service`), cases, priority, admin list/detail/decision/restore, conflict of interest, `photo_decisions` + audit, `PhotoModerationDecided`; identity `set_role` + CLI with audit (moderator and admin, Q34)                                                                                           | B19, B23 (for `feed.service.is_public`) |
| B23   | BE                          | Feed projection: models, `sync_post`, handlers, `feed.service` (`is_public`, `can_view`, `visibility`)                                                                                                                                                                                                                                                                   | DB6, B18                                |
| B24   | BE                          | Feed reads: tabs with cursors, composition, deed page, public page, profile deeds, saved, `social`; Top snapshot job per period, week / month / year (Q25, Q26 decided)                                                                                                                                                                                                  | B23, B20                                |
| B25   | BE                          | Feed writes: likes, saves, comments + `_kindness.py` (Q35 decided: English pack only, packs per language; Q37 decided: links and phone numbers blocked; Q38 decided), comment deletion and erasure job, one-time notices (`/api/feed/me/notices`, Q41), hide/show with audit (separate hides, Q24), shares, follows, remove follower; events                             | B23                                     |
| B26   | BE                          | `tests/test_architecture.py`: `media.service` and media private files never import `feed`; no future `scoring`/`points`/`rewards`/`leaderboard` module may import `feed`                                                                                                                                                                                                 | –                                       |
| B27   | BE                          | Comment reports and removal (Q38): `comment_report_cases`, `comment_reports`, `comment_decisions` in migration 0015; report endpoint; admin list/detail/decision/restore with conflict check; retention rules in delete and the erasure job; `CommentModerationDecided`; author notices (list + seen)                                                                    | B25, B22                                |
| F9    | FE                          | API clients and types for §3, `RequireRole`, routes (§6.2), Home switch                                                                                                                                                                                                                                                                                                  | – (contract)                            |
| F10   | FE                          | Home feed: tabs, card, carousel, skeleton, empty and end states, optimistic like/save/follow, share sheet, post menu, signed-URL refresh, onboarding prompt                                                                                                                                                                                                              | F9                                      |
| F11   | FE                          | Deed page: visibility row, hide dialog, hidden banner, comments list, composer and errors, delete dialog                                                                                                                                                                                                                                                                 | F9, F10 (card)                          |
| F12   | FE                          | Public page and unavailable state; sign-up handoff with `next`                                                                                                                                                                                                                                                                                                           | F9, F10                                 |
| F13   | FE                          | Profiles: other profile (follow, 4 counts, deeds), own profile line, connections, Saved                                                                                                                                                                                                                                                                                  | F9, F10                                 |
| F14   | FE                          | Photo upload: `resizeImage`, `usePhotoUploads`, `PhotoPicker` on the deed form, self-log form and My deeds detail (add/remove inside the window), attach after send, result line                                                                                                                                                                                         | F9                                      |
| F15   | FE                          | Blur editor                                                                                                                                                                                                                                                                                                                                                              | F14                                     |
| F16   | FE                          | Report-photo sheet (members and visitors) and outcome banners in My deeds; `/photo-guidelines` (Q39 decided; guidelines copy needs PO approval)                                                                                                                                                                                                                          | F10, F12, F14                           |
| F17   | FE                          | Admin queue, review, remove dialog, done banner, restore                                                                                                                                                                                                                                                                                                                 | F9                                      |
| F18   | FE                          | One-time public-feed notice on the feed (Q41), with the notices endpoints from B25                                                                                                                                                                                                                                                                                       | F10                                     |
| F19   | FE                          | Report-comment sheet; admin Comment reports queue and review (reusing F17 components) (Q38); comment-removed card on the feed; `/community-guidelines`                                                                                                                                                                                                                   | F11, F17                                |
| QA5   | QA                          | Backend tests §8 "Feed", "Social", "Media", "Reports and admin", "Config", "Events"                                                                                                                                                                                                                                                                                      | B18–B27                                 |
| QA6   | QA                          | Pipeline privacy tests with fixture images (GPS EXIF, XMP, ICC, orientation 6/8, PNG with alpha, animated WebP, bomb, wrong magic)                                                                                                                                                                                                                                       | B20                                     |
| QA7   | QA                          | Frontend tests §8 "Frontend"                                                                                                                                                                                                                                                                                                                                             | F10–F19                                 |
| SEC2  | SEC                         | Review against §9                                                                                                                                                                                                                                                                                                                                                        | QA5, QA6, QA7                           |
| AD3   | AD                          | Diagrams: new `feed` and `media` modules and their imports, storage (GCS / SeaweedFS), upload and processing flow, report and decision flow, feed projection events; `docs/architecture/README.md`                                                                                                                                                                       | SEC2                                    |
| DOC3  | main session                | After PO answers: PRD §6.5 wording (Q23), PRD §12 new limits, CLAUDE.md (modules `media` and `feed`, storage gotchas, SeaweedFS), ROADMAP M3 line                                                                                                                                                                                                                        | PO OK                                   |
| OPS1  | main session / later DevOps | GCS bucket, service account, HMAC key, CORS, lifecycle, secret wiring (§4.2)                                                                                                                                                                                                                                                                                             | before launch; needs GCP access         |

**Order and parallelism**

- **Start together:** B17, DB4, DB5, B26, F9. (DB6 follows DB4 because it backfills from verified deeds; it can be written in parallel and merged after.)
- **Backend chains:** DB4 → B18 → B23 → (B24 and B25 side by side). DB5 + B17 → (B19 and B20 side by side) → B21. B22 after B19 and B23.
- **Frontend:** after F9, run F10, F14 and F17 in parallel (separate folders); F11, F12, F13 follow F10; F15 follows F14; F16 after F10, F12 and F14; F18 last. The frontend builds against §3 with fetch mocks and doesn't wait for the backend.
- **QA:** QA6 as soon as B20 lands (it's the highest-risk privacy code); QA5 writes tests from §8 while B tasks land; QA7 after the F tasks.
- **End:** SEC2 after QA5–QA7; AD3 after SEC2; DOC3 after the PO's review.

---

## 8. Test plan (qa-engineer)

### Feed

- A QR deed appears in Nearby after its `DeedVerified` handler runs; a pending (invite) deed and a self-log never appear; an unverified (expired invite) deed never appears.
- Withdrawing a verified deed removes it from every tab, the deed page (404), Saved, the profile list and the public page **immediately**, even before the `DeedWithdrawn` handler runs (live check, D37).
- Handlers in any order: `DeedWithdrawn` processed before `DeedVerified` leaves the post `withdrawn`; re-running either handler changes nothing (idempotent).
- Nearby order `published_at DESC, deed_id DESC`; pagination over 45 posts with page size 20 returns every post exactly once, including posts with identical `published_at`; a post published between page loads doesn't duplicate items; a bad cursor gives 400 `invalid_cursor`.
- Following: only deeds of followed doers and followed reporters (Q25 = A); unfollowing removes them; empty when following nobody.
- Top, for each of `week`, `month`, `year`: score = likes from others + 3 × comments with the same weights from config (publish 2/5 → order changes in every period); only posts published in the last `top_{period}_days` (deeds at day 6, 29 and 364 vs day 8, 31 and 366: each appears exactly in the periods whose window contains it); a deed published 10 days ago that gets 30 new likes today is **not** in week but is in month and year; score uses total likes and comments, including those made before the window's start; `period` missing → week, `period=decade` → 422, `period` on another tab → 422, a week cursor sent with `period=month` → 400; each period keeps only its newest two snapshots; changing `top_month_days` by `publish` changes the month window without a code change; a deleted comment lowers the score at the next refresh; equal scores: the newer deed ranks higher (then `deed_id`), in every period; hidden or withdrawn posts disappear at once even inside a snapshot; paging through a snapshot while a new one is computed finishes on the old snapshot; `rank` returned; badges only up to `top_badge_ranks` (frontend).
- Visibility: doer hides → gone from all lists and the public page (404), still visible to doer and reporter on `/deeds/:id`, likes/comments counters unchanged; with separate hides (Q24 = A), the reporter can't show a deed the doer hid (and vice versa), and the deed is listed again only when both hides are cleared; each hide/show writes one `audit_log` row; **no `deeds` column, `same_act_groups` counter, `deed-counts` value, `verification_events` row or prize field changes** (assert before/after snapshots); hide on a withdrawn deed gives 409; a stranger gets 404.
- Area name (Q28): a deed inside a ward polygon shows that ward's display name; a point on a shared border always gets the same ward (lowest `id`); outside every polygon but within 1500 m shows the nearest ward; more than 1500 m from every ward → "Bangalore"; no location → "Bangalore"; a lookup that raises (simulated database error) → deed still created, `area_id` null, "Bangalore", and the log line has no coordinates; changing `area_fallback_m` by `publish` changes the fallback distance.
- Area seed: every row has a non-empty `source`; no display name contains a ward number or code (`/\bward\b/i`, `/^\d+$/`, the dataset's code), or is longer than 40 characters; `ward_code` never appears in any API response; `AREAS_SOURCE.md` exists and names a licence.
- No outbound geocoding: during the deed and feed tests the HTTP client is patched to fail on any request to a host other than the test storage stand-in; nothing calls out.
- Coordinates: **no response anywhere in M3 contains `lat`, `lng`, `location` or a coordinate-looking pair** (one test walks every new endpoint's JSON).
- Public page: works without auth; only for listed verified deeds; the same 404 body for missing, hidden, withdrawn and random IDs; `no-store` and `noindex` headers; the `/d/:id` HTML head has the generic Open Graph tags and robots meta for every deed, and contains none of the deed's doer name, area, category, description or photo URL (test with a listed deed that has photos); the share text has no name or area (Q40); rate-limited per IP; no `liked`, `saved` or viewer fields.

### Social

- Self-likes (Q43): the doer and the reporter can like their deed (200, `liked: true`); `like_count` goes up by 1 each and is shown; `top_like_count` and the Top score don't change; a third member's like raises both; unliking reverses exactly what the like did; `top_like_count` always equals the number of `feed_likes` rows whose user is neither the doer nor the reporter (checked after a mixed concurrent run).
- Like/unlike idempotent; `like_count` equals the number of `feed_likes` rows after concurrent likes from 10 users and repeated like/unlike by one user (barrier test); liking a non-viewable deed → 404.
- Comments: 300 characters accepted, 301 refused (value from config: publish 280 and retest); empty refused; filter blocks a listed English term, its leetspeak, repeated-letter, mixed-case and spaced-out forms; **language packs (Q35):** with only `en` enabled, a term that exists only in a test `hi` pack passes; publishing config with `hi` enabled (test pack with a Devanagari and a Latin-transliterated term) blocks both without a code change; a term in one pack never matches through another pack's substitutions; enabling a code with no pack, or a pack with an unknown key, fails config validation; no module outside `_kindness.py` and the config references a language code (architecture test); does **not** block "Scunthorpe"-style substrings of short terms; links and 10-digit numbers (with `+91`, spaces, dashes) refused while the flags are on and accepted when off; the error doesn't name the word.
- Comment rate limit: the 6th comment in a minute gets 429; account and IP keys trip independently.
- Delete: author ✓, doer ✓, reporter ✓, other member 404; `comment_count` decreases once even if deleted twice; author deletion erases the body at once; doer/reporter deletion keeps it until the erasure job runs after the retention period.
- Follows: self-follow 400; idempotent; `max_following` boundary; remove follower deletes their follow and emits nothing; counts on `/social` match; other people's follower and following lists aren't available (Q36 = A): no route takes a handle for lists (`/api/users/{handle}/followers` and `/following` → 404), `/social` returns counts only, and on another person's profile the counts aren't links (frontend test).
- Saves: private (no endpoint returns another user's saves or a save count); a saved deed that is hidden drops out of Saved and returns when shown again.
- Shares: recorded with the target; a hidden deed gives 409.
- Onboarding (Q42): a signed-in user without a verified phone can read all three tabs, deed pages, comments and profiles, but gets 403 `onboarding_required` on like, save, comment, follow, hide, share recording and comment report; reporting a photo works for them under the visitor IP limits.

### Media

- Create: type not in the allowlist → 400; size over the limit → 400; the 13th pending upload → 429; photos disabled → 403.
- Presigned PUT signs `Content-Type` and `Content-Length` (inspect the URL's `X-Amz-SignedHeaders`); integration test against SeaweedFS (marked, runs when `GD_TEST_STORAGE_URL` is set): a wrong length or type is rejected by the store.
- Complete: missing object 409; size mismatch → `rejected` and object deleted; a PNG declared as JPEG → `rejected`; repeat call idempotent; another user's upload 404.
- Attach: 4 photos OK, 5th → 409 `too_many_photos`; two concurrent attaches at 3/4 → exactly one succeeds (advisory lock); after 24 h → 409 `photo_window_closed` (boundary ±1 s, value from config); withdrawn deed → 409; doer or stranger → 404; another user's upload → 409; a self-log attach with blur regions → 422; regions out of range, too small or too many → 422.
- Admin-removed photos don't count toward the 4, so the uploader can add a replacement inside the window.
- Delete: uploader inside the window ✓; uploader on day 3 (after the window) ✓ while adding on day 3 → 409 `photo_window_closed` (Q30); the photo's issued URLs return 404 right after (same immediate cut-off as Q29); no `deeds`, count, verification, points or prize change (before/after snapshot); stranger 404; open case closed; objects deleted (or retried by the job).
- Doer removal (Q29): the doer removes a photo the reporter uploaded, inside and after the 24-hour window ✓; the other photos stay in order and the deed stays listed; the photo is gone from the feed, `/deeds/:id`, the public page, `GET /api/media/deeds/{id}/photos` and admin thumbnails; a signed URL issued **before** the removal returns 404 from storage right after it (SeaweedFS integration test); a failed object delete is retried by `media.purge_deleted`; one `audit_log` row `photo.doer_removed` with the doer as actor and no image or URL in it; one `PhotoRemovedByDoer`; the reporter's `outcomes` shows `removed_by_doer` with the doer's `UserRef`, the doer's view doesn't; **no `deeds` column, `feed_posts` row, count, verification event, points, leaderboard or prize field changes** (before/after snapshot); admin restore → 409 `not_restorable`; the reporter re-uploading the same file to that deed → `rejected` `removed_by_doer`, a different file inside the window ✓; a member who isn't the doer, the reporter on a photo they didn't upload, and an admin on this endpoint → 404; the doer on a self-log photo → 404; an open report case on the photo → `closed`.
- Self-log photos: owner only (others 404 on every endpoint); never in any feed, public or admin response; deleting the self-log detaches and the job deletes the objects.

### Pipeline privacy (QA6, fixture images)

- A JPEG with GPS, camera and time EXIF, XMP and an IPTC block → every variant has **no EXIF, no XMP, no ICC, no comment** (reopen with Pillow and also scan the raw bytes for `Exif`, `GPS`, `http://ns.adobe.com/xap`).
- EXIF orientation 6 and 8 produce upright variants; blur regions given in the upright frame land on the right pixels (compare a marked fixture).
- Blur: the region's pixels differ strongly from the original (mean absolute difference over a threshold) and pixels outside the feathered edge are unchanged; the original high-frequency detail can't be recovered by a sharpen filter (simple check: Laplacian variance inside the region below a threshold); **no variant is ever written before blur** (the pipeline order test).
- The staging object is deleted after success and after a rejection; on a crash between commit and delete, `media.sweep_staging` / `purge_deleted` remove it.
- No unblurred original is kept (Q31): after processing a photo with blur regions, listing the bucket shows only the blurred variants under `photos/{id}/` (no staging object, no `original`/`raw` key, nothing in `held/`); every stored variant's pixels inside each region differ from the source fixture beyond a threshold; an admin-held copy is the blurred variant; no endpoint (owner, admin or public) can return unblurred pixels; there is no API to change the blur on an attached photo (remove and re-add instead).
- PNG with alpha → flattened RGB; animated WebP → `animated`; 50 MP image → `too_many_pixels` without allocating it (DecompressionBombError); truncated file → `corrupt`; a 100×100 image → `corrupt` (min side).
- Variants respect `max_side_px` from config and keep the aspect ratio; `width`/`height` stored.
- Worker: concurrent workers never process one photo twice (SKIP LOCKED); a stuck `processing` row returns to `queued`; after 3 attempts → `failed`.
- Signed GET URLs: two calls in the same 30-minute step return the identical URL; the next step returns a new one; `urls_expire_at` = signing time + 60 min; the signer's output equals botocore's for a frozen time.

### Reports and admin

- Visitor report on a public deed's photo → 202; on a hidden deed's photo → 404; on a self-log photo → 404; on a removed photo → 404; uploader on own photo → 400.
- Duplicates (same member, or same visitor IP) → 202 and no new row; different members → one case, `report_count` 2.
- Priority (Q44): `child_no_guardian_consent` → 1; `not_consented`, `private_details` and `inappropriate` → 2 (equal; ordered by oldest among them); `other` → 3; a child report in a case of privacy or harm reports raises it to 1; an `inappropriate` report raises an `other` case to 2; priority never goes down when a less serious report joins; queue order 1 → 2 → 3, then oldest; the chip is Child / Privacy/Harm / Other. Reports of any priority leave the photo visible until a moderator or admin decides.
- **A report never changes the photo, the deed, `feed_posts`, counts or anything scored** (before/after snapshot).
- **The uploader can't detect an open report:** the owner photo endpoint's JSON is byte-identical before and after a report (except signed-URL timing, which the test freezes); after resolution the outcome appears without the reporter's identity or the details.
- Roles (Q34): a member → 403 on every photo-report endpoint; a moderator and an admin can list, review, keep, remove and restore; a moderator or admin who is the deed's doer, reporter or the photo's uploader → 403 `conflict_of_interest` on decide **and** restore; `set-role` via the CLI writes one `audit_log` row (`role.changed`, with the reason) and the person's existing tokens stop working; there is no HTTP route that changes a role (any attempt 404/405).
- Decision: member → 403; missing or 4-character note → 422; keep → photo unchanged, case resolved; remove → photo gone from feed, deed page, public page and admin-list thumbnails for non-admins, still visible to admins; remove → a URL issued before the decision returns 404 from storage right after it (SeaweedFS integration test), and the admin review URL (`held/` key) still works; restore → back everywhere with **new** URLs, while a URL from before the removal still returns 404; a failed move is retried by `media.move_objects`; every decision writes one `photo_decisions` row, one `audit_log` row and one `PhotoModerationDecided`; UPDATE/DELETE on `photo_decisions` raises; a second decision on a resolved case → 409; an admin who is the doer, reporter or uploader → 403 `conflict_of_interest`.
- Retention (Q32 = 90 days): restore on day 89 ✓ (new URLs); on day 91 after `media.purge_removed` ran → 409 `not_restorable`, the `held/` objects are gone from storage, `objects_purged_at` is set, and the `photos` row, report case, `photo_decisions` and `audit_log` rows remain (no image); a restored-then-removed-again photo counts 90 days from the latest removal; changing `removed_photo_retention_days` by `publish` changes the cut-off without a code change.
- A new report on a kept photo opens a new case that shows the earlier decision.
- Rate limits: visitor IP hourly and daily keys; member key.
- Role CLI: changes the role, writes `audit_log`, bumps `token_version` (an old token with the old role is refused).

- **Public-feed notice (Q41):** a new user and an existing user both get `["public_feed_intro"]`; after `seen` it's gone on every device and session; `seen` twice → 204; unknown key → 404.
- **Comment-removed notice:** after a moderation removal the author's `comment-notices` lists it (with the excerpt, no reporter, moderator or note); `seen` hides it, is idempotent, and 404s for someone else's comment; a restore removes it from the list; it expires after 30 days; author, doer and reporter deletions never create a notice.
- **Comment deletion and reports (Q38):** the author deletes their comment → `body` NULL in the same transaction (no admin can read it); the doer or reporter deletes someone's comment → gone for everyone, `body` kept, readable only on the admin detail, erased by the job after 30 days (day 29 kept, day 31 NULL); another member, and a moderator or admin using the delete endpoint, → 404; a member reports a comment → 202, the comment stays visible, the author sees no change; reporting own comment, a deleted comment, or one on a deed the caller can't view → 404; duplicate report → 202 and no second row; priority = the most serious reason; a member → 403 on admin comment endpoints, moderator and admin ✓; conflict of interest (author, doer, reporter) → 403 on decide and restore; remove → gone from every list, `comment_count` −1, `deleted_role = 'moderation'`, one `comment_decisions`, one `audit_log`, one `CommentModerationDecided`; restore within 30 days → back, count +1; after erasure → 409 `not_restorable`; author or doer deleting while a case is open → case `closed`; UPDATE/DELETE on `comment_decisions` raises; `author_removed_last_90_days` counts only moderation removals.

### Config

- `media` v1 and `feed` v1 validate (all keys, no extras); `deeds` v5 equals v4 minus `photos_enabled` plus `area_fallback_m`; `DeedLimits` rejects a snapshot with `photos_enabled`; downgrades append, never delete.
- Changing a value by `publish` changes behaviour without a code change: photos per owner, edit window, comment length, Top weights, Top windows and refresh intervals per period, rate specs.
- No Top change (weights, windows, a period's snapshot) changes any `deeds` column, `deed-counts` value, points, rank, prize field or leaderboard (before/after snapshot).

### Events

- `DeedLiked`, `CommentPosted`, `UserFollowed` only on real inserts; no payload contains text, coordinates or phone numbers; feed subscribes to exactly `DeedVerified`, `DeedWithdrawn`, `DeedRejected`.

### Frontend

- Home: signed out shows the welcome page; signed in shows Nearby; tabs switch and show their intros; skeleton, empty Following ("You're not following anyone yet" → Browse Nearby), end-of-feed message; infinite scroll loads the next cursor.
- Removed photos (Q33): a deed with 3 photos, one removed → the card, deed page and public page render 2 photos with "1 / 2" and two dots, no placeholder or "removed" text; all removed → no carousel, text-only card; the same for doer, uploader and admin removals; no API field reveals that a photo was removed except the uploader's own `outcomes`.
- Card: doer first, "recognised by …", category · area · time, Verified chip, carousel counter and dots, like/save `aria-pressed` and optimistic rollback on error, Follow hidden on own deeds, Top badges for ranks 1–3 only, with the period's wording.
- Top period dropdown: only on the Top tab; three options with their rolling labels exactly as in §6; default week; changing it sets `?period=`, refetches from rank 1 and leaves the tab count at three; reloading or going back keeps the period; the intro line and badge wording follow the period.
- Share sheet: WhatsApp link encodes the text and URL; Copy link writes the clipboard; "More options" only when `navigator.share` exists; each option posts a share.
- Deed page: hide dialog copy and the hidden banner; Show in feed; Delete visible only where `can_delete`; the doer/reporter note; filter, rate-limit, link and phone errors inline; counter at 300.
- Public page: no action buttons, join section, unavailable state, signed-in redirect to `/deeds/:id`.
- Profiles: Follow toggles the count; on another person's profile the follower/following counts are plain text, not links (Q36); connections tabs; Remove follower; Saved list and its lock note.
- Photos: picker hidden when `photos_enabled` is false; 4-photo limit hides Add; upload progress, retry and type/size errors; **the storage PUT has no `Authorization` or `X-Device-Id` header**; Send waits for uploads; attach after send with regions; attach failure line on the result.
- Blur editor: add, drag, resize (pointer and keyboard), remove, Cancel discards, Done sets "Blurred"/"Edit blur"; regions sent as fractions.
- Report sheet: Send disabled until a reason; sent state; the menu hides Report photo for the uploader and on cards without photos.
- My deeds: photo grid, add/remove inside the window, window-closed state, removed and kept banners with the reason text, withdrawn clause.
- Public-feed card: shown above the feed when the key is unseen; [OK] and [See my deeds] both call `seen` (the latter then opens My deeds); not shown again after reload.
- Comment-removed card: shown once per removed comment above the feed, [OK] calls `seen` and it doesn't return after reload; guidelines link.
- Report-comment sheet: "⋯" not shown on my own comments; reasons and optional details; thank-you state; the comment stays in the list. Admin Comment reports: queue order and chips, review with note required, Keep/Remove, Restore while restorable.
- Admin: RequireRole hides `/admin` from members (NotFound) and shows the photo-report pages to moderators and admins; queue order and chips; review shows reports with member handle / visitor label; note required before Keep or Remove; remove dialog; done banner and Restore.
- Signed-URL refresh: an image error triggers one refetch.

---

## 9. Security and privacy concerns (security-reviewer)

**Location and personal data**

- No endpoint, event payload, log line or object metadata contains coordinates, phone numbers, emails or user IDs (only `UserRef`). Area names come from the deeds module only.
- EXIF/XMP/ICC stripping is total on every variant (QA6). The raw upload with its metadata lives only in `staging/` until processed; check the staging deletion paths and the bucket lifecycle backstop.
- Check that log lines (`app/core/log_redaction.py`) never include signed URLs (they are bearer credentials for an hour) or comment bodies.

**Storage**

- Bucket private, public access prevention enforced, uniform access; HMAC key scoped to one bucket; secret in the secret store, never in the repo or images.
- Presigned PUT: short TTL, `Content-Type` and `Content-Length` signed, key chosen by the server (`staging/{uuid}`), no client-chosen paths; a user can only complete or attach their own upload.
- Presigned GET: issued only by `media.service` after a visibility check; lifetime ≤ 60 min (config) is the maximum exposure after hide or withdraw. Any removal (admin, doer, uploader) cuts off issued URLs at once by moving or deleting the objects (PO decision 2026-11-19).
- The frontend never sends the access token or device ID to the storage host (test).
- CORS on the bucket: only the web origin, only `PUT`/`GET`, only `Content-Type`.

**Image parsing**

- Pillow on untrusted input: pin a current version, `MAX_IMAGE_PIXELS`, format allowlist, `verify()` then reopen, no SVG/HEIC/TIFF/GIF decoders reachable (format check before any heavy work), processing in the worker, not the API.
- Magic-byte check before processing; size checked twice (signed length and HEAD).

**Blur**

- Pixelate-then-blur is irreversible; no unblurred derivative is ever written; no unblurred copy kept (D33, Q31). The blur preview in the browser is cosmetic only.

**Reports and admin**

- Visitor reports are unauthenticated writes: IP hourly and daily limits now; CAPTCHA before launch (added to the before-launch list). Report details are free text seen by admins only; render as text, never HTML.
- The uploader learns nothing about open reports (test compares responses); outcomes never include the reporter or details.
- Admin endpoints: `require_role` on every route; conflict-of-interest check; every decision audit-logged with a note; roles only via CLI with audit and `token_version` bump. Before launch: 2FA for admin accounts.
- Admin photo URLs for removed photos are signed the same way; check they are never returned on non-admin routes.

**Feed and social**

- IDOR: every deed, comment, photo, self-log and case route checks ownership or visibility and answers 404 (not 403) to outsiders; hidden deeds are invisible to non-parties, including through likes, saves, shares, comments and reports.
- The public page returns one body for every unavailable case (no oracle for hidden vs withdrawn vs nonexistent).
- Live status check on every read (a withdrawn deed is never served from the projection).
- Visibility can't reach scoring (architecture test); hiding writes nothing to `deeds`.
- Counters can't be pushed by repeated requests (idempotent likes; follow/like/comment rate limits). Top (all three periods) can still be gamed by a ring of accounts; it is display only (PRD), and M5 can watch it. The year list exposes nothing new: it shows only listed deeds, as Nearby already does.
- Comment filter evasion is expected; it is a basic filter (PRD), moderation comes in M7.
- `X-Robots-Tag: noindex, nofollow` and generic link previews on public pages (Q40) so search engines and chat apps never hold a deed's name, photo or text that we can't take back after a hide or removal.

**Compliance (to confirm with legal before launch; PRD §15)**

- No consent tick at upload (PO decision); children's photos under DPDP and guardian consent; review time limits for reports.
- IT Rules 2021: a grievance officer and takedown timelines for user content; how child-safety reports are escalated.
- Retention: removed photos (90 days, Q32; confirm with legal), deleted comments (Q38), upload records (kept as rows without images).

**Gaps carried forward**

- Identity actions other than role changes still don't write `audit_log` (M2 #2).
- The architecture test misses relative imports (M2 #5); new modules must use absolute imports.

---

## 10. Before-launch list (additions for ROADMAP)

- GCS bucket, service account, HMAC key, CORS, lifecycle, secrets (OPS1).
- CAPTCHA on visitor photo reports once IP thresholds are hit (with the M1 CAPTCHA item).
- 2FA for admin and moderator accounts.
- Legal: photo consent and children (already listed), report review time limits, IT Rules 2021 grievance officer, retention periods.
- Web server / hosting: send `X-Robots-Tag: noindex, nofollow` on `/d/*` page responses (Q40; the app also adds a robots meta tag, and `/api/public/*` already sends the header). Set `og:image` in `frontend/index.html` to the absolute production URL of `/og-logo.png` (some preview crawlers ignore relative URLs).
- PO approval of the photo-guidelines page copy (Q39).
- PO approval of the community-guidelines page copy (comment-removed notice).
- PO review of the English kind-words starter list (Q35). Hindi, Kannada and other languages are a later decision: each needs a pack written by native speakers, then is enabled by config.
- Area names (Q28): written confirmation from OpenCity/GBA of the ward data's source and terms (PO decision 2026-11-19: used meanwhile under OpenCity's public-domain label, credited on `/terms`); confirm no newer ward delimitation; re-check the KML hash from a browser download; PO review of the simplified ward names.

---

## 11. Questions for the PO

Each question has an example, options and the architect's recommendation. The design builds the recommendation unless the PO picks otherwise; all answers are recorded in the table (2026-11-18 and 2026-11-19).

| #   | Question                                                                                                                                                                                                                            | Example                                                                                                                               | Options                                                                                                                                                                                                                                                    | Recommendation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Q23 | Do only verified deeds appear in the feed? The F7 notes say "unverified deeds never appear", but PRD §6.5 still says "Each post shows its status: Verified or Pending" and §10.1 says a new deed is "shown in the feed as Pending". | Priya invites Ahmed by link (when invites return). Before Ahmed accepts, does the deed show in Nearby as "Pending" without his name?  | A. Verified only; update PRD §6.5 and §10.1. B. Also show pending deeds without the doer's name.                                                                                                                                                           | **A.** Matches the approved canvas, and invites are off anyway. **PO: A (2026-11-18). PRD §6.5, §10.1 and the Post entity updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Q24 | When the doer and the reporter disagree about hiding, who wins? The canvas says "You or Ahmed can show it again at any time."                                                                                                       | Ahmed hides his deed because he'd rather keep it private. Priya, who reported it, taps "Show in feed". Does it come back?             | A. Each person's hide is separate; the deed is in the feed only when neither has hidden it, and each can undo only their own. B. One shared switch; either can hide or show (as drawn).                                                                    | **A.** A doer's privacy choice shouldn't be undone by someone else. The hide dialog copy changes slightly (§6.3). **PO: A (2026-11-18): only the person who hid it can show it again, in both directions (follow-up: the doer cannot override the reporter's hide either). Hiding never affects verification, points or leaderboard credit. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Q25 | Which deeds does the Following tab show?                                                                                                                                                                                            | Priya follows Arjun. Arjun reports a deed Sneha did. Does it appear in Priya's Following tab?                                         | A. Deeds where someone I follow is the doer or the reporter. B. Only deeds where they are the doer.                                                                                                                                                        | **A.** More to see while the app is young, and recognising others is activity worth following. **PO: A (2026-11-18). Cards must clearly distinguish the doer from the reporter (credit and recognition stay fair). PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Q26 | What does "Top this week" rank?                                                                                                                                                                                                     | Kavya's deed from 10 days ago gets 30 new likes this week. Ahmed's deed from yesterday has 12. Who is higher?                         | A. Deeds published in the last 7 days, ranked by all their likes + 3 × comments (Ahmed is in, Kavya isn't). B. Any deed, ranked by likes and comments made in the last 7 days (Kavya first).                                                               | **A.** Simple, matches "the most appreciated deeds of the last 7 days", keeps the tab fresh. Rank chips "#1–#3 this week" only. **PO: A, extended (2026-11-19): one Top tab with a dropdown: Top this week (last 7 days) · Top this month (last 30 days) · Top this year (last 365 days). Rolling windows, not calendar periods. Same configurable formula for every period (likes + 3 × comments); only deeds published inside the window are eligible; their total likes and comments count; a deed outside the window never appears, even with new engagement; equal scores → newer deed first. Display only: no change to points, impact or verified-deed scores, prizes or the leaderboard; privacy, moderation and visibility rules unchanged. Kept in M3. PRD §6.5 and ROADMAP updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Q27 | When many witnesses report the same act, does each report get its own post?                                                                                                                                                         | Five people at the Koramangala blood donation camp all scan Ahmed. Does Nearby show five near-identical cards?                        | A. Each verified report is its own post (simplest; each has its own likes, comments and hide). B. One post per act, "recognised by Priya and 4 others".                                                                                                    | **A for M3**, revisit if it happens often. B needs rules for likes, hides and photos across several reporters. **PO: A (2026-11-19): one post per verified report for M3; each has its own likes, comments, photos and hides. Merging same-act posts can be revisited after launch (same-act grouping data is kept).**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Q28 | Where do area names come from, and how fine are they?                                                                                                                                                                               | Ahmed's deed was at a junction in Koramangala 4th Block. Should the card say "Koramangala", "Koramangala 4th Block" or the ward name? | A. BBMP ward boundaries (about 240 areas), names lightly cleaned up, open data with attribution. B. A hand-made list of about 80 well-known localities. C. A paid reverse-geocoding service.                                                               | **A.** Covers the whole city, names people recognise, no third party ever sees coordinates. Not finer than a ward, to protect where people live. **PO: A (2026-11-19): latest officially published Greater Bengaluru ward boundaries with a suitable licence; familiar simplified names (no ward numbers); never more precise than ward level; coordinates stay on our server and are never sent to an external geocoder; no API or card exposes coordinates, street, building or address; outside every ward and more than 1.5 km from the nearest → "Bangalore"; any mapping failure → "Bangalore", never a guess; verify source, currency and licence, with attribution on `/terms` if required. PRD §6.5 and ROADMAP updated.** **Dataset (DATA1, 2026-11-19):** OpenCity "GBA Final Wards Map" (Dec 2025, 369 wards of the five GBA corporations), labelled "Other (Public Domain)" by OpenCity, source GBA; no licence published by GBA or the state. **PO decision (2026-11-19): use it now, credit it on `/terms` ("Ward boundaries: Greater Bengaluru Authority, via OpenCity (data.opencity.in)"), and ask OpenCity/GBA to confirm provenance and terms in parallel, before launch.** Details: `backend/app/modules/deeds/data/ward_candidate/AREAS_SOURCE.md`. |
| Q29 | Can the doer remove photos of themselves?                                                                                                                                                                                           | Priya posts three photos of Ahmed helping. Ahmed dislikes one. Can he delete it himself, or must he report it?                        | A. Yes, the doer can remove any photo on a deed about them; it's final, and Priya sees "Ahmed removed 1 photo". B. No, he hides the whole deed or reports the photo.                                                                                       | **A.** It's his own image; a report would make an admin do something he can do safely himself. **PO: A (2026-11-19): the doer can remove any photo on a deed about them, whoever uploaded it; only that photo goes, the deed stays; removal is permanent from the feed, deed page and share page, and the uploader can't restore it; the uploader sees a neutral notice ("Ahmed removed 1 photo from this deed."); an internal audit record is kept without the image; no effect on the deed, report ownership, verification, points, leaderboard or prizes; nobody else gains removal rights; issued URLs stop working at once (objects deleted). PRD §6.5 and ROADMAP updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Q30 | Can the uploader remove their own photo after the 24-hour window?                                                                                                                                                                   | On day 3 the elderly man's son asks Priya to take a photo down. Can she?                                                              | A. Removing is allowed any time; adding only within 24 h. B. Both only within 24 h (she must report it).                                                                                                                                                   | **A.** Taking a photo down should never be harder than putting it up. Points are not affected (M4 to confirm). **PO: A (2026-11-19): the uploader can remove their own photos at any time; adding stays limited to 24 hours. Removal works like the doer's (only that photo, links stop at once, no effect on the deed, verification, points, leaderboard or prizes). PRD §6.5 and §12 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Q31 | Keep the unblurred original privately?                                                                                                                                                                                              | Priya blurs the elderly man's face. Do we keep the unblurred photo somewhere?                                                         | A. Never keep it; the blur is final; to change it, remove and re-add the photo (her phone still has it). B. Keep it privately for 24 h so she can edit the blur. C. Keep it for admins for 90 days.                                                        | **A.** The unblurred face is exactly what someone asked us not to show; not storing it removes the risk. **PO: A (2026-11-19): never keep the unblurred original, for anyone, for any period. The blur is final once sent; to change it the uploader removes the photo and adds it again from their phone (re-adding only inside the 24-hour window, so after it the only fix is removal). The raw upload exists only in staging until processing and is then deleted. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Q32 | How long are admin-removed photos kept (so they can be restored)?                                                                                                                                                                   | An admin removes a photo of a child on 1 Dec, then learns the parent agreed. Can it be restored on 20 Dec? On 1 April?                | A. 90 days, then deleted for good. B. 30 days. C. Forever.                                                                                                                                                                                                 | **A.** Long enough to fix a mistake; not kept forever. **PO: A (2026-11-19): admin-removed photos are held privately (blurred form, admins only, no public links) for 90 days from the removal, restorable during that time, then deleted for good; the record of the removal stays without the image. Config value `removed_photo_retention_days`. PRD §6.5 and §12 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Q33 | What do viewers see where a photo was removed?                                                                                                                                                                                      | One of Ahmed's three photos is removed. Do viewers see "A photo was removed"?                                                         | A. Nothing: the deed shows the remaining photos (or none). B. A grey placeholder "Photo removed after review".                                                                                                                                             | **A.** A placeholder invites questions and draws attention to the person in it. The uploader is told in My deeds. **PO: A (2026-11-19): viewers see nothing where a photo was removed (by the doer, the uploader or an admin): the remaining photos only, counter included, or a text-only deed if none remain. Only the uploader is told, in My deeds. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Q34 | Who can review photo reports, and how do people become admins?                                                                                                                                                                      | Can Rahul, a trusted volunteer, review reports without access to anything else later added to the admin area?                         | A. Roles `admin` and `moderator` can both review; roles are set by the engineering team with a command, recorded in the audit log; no one decides a case about their own deed. B. Only `admin`.                                                            | **A.** Lets a moderator handle reports without full admin powers later (M5). Before launch, admin accounts need 2-step login. **PO: A (2026-11-19): moderators and admins both review photo reports (keep, remove, restore); roles are given only by the engineering team with a server command, audit-logged with a reason, and the person is signed out; nobody decides or restores a case on a deed they did, reported or uploaded photos to; later admin areas are admin-only unless decided otherwise; two-step login for moderators and admins before launch. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Q35 | Which languages does the kind-words filter cover, and who writes the list?                                                                                                                                                          | Someone comments "chutiya" or the Kannada equivalent, in Latin letters or in Devanagari/Kannada script.                               | A. English, Hindi and Kannada, each in Latin transliteration and in its own script; the list written and kept up to date by native speakers the PO chooses, changed through config. B. English only for now.                                               | **A**, with a short starter list from us until the real list arrives. **PO: B, extensible (2026-11-19): English only for M3. Each language is a separate pack (its own blocked words and rules) in the `feed` config; the enabled languages are a config list (`["en"]` in M3), nothing else hardcodes English; Hindi, Kannada and others can be enabled later by adding a pack, without major code changes. Spelling variations and simple bypasses (look-alike characters, spaced or repeated letters) are still caught. The friendly block message and comment deletion controls are unchanged. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Q36 | Can people see other people's follower and following lists?                                                                                                                                                                         | Rahul opens Ahmed's profile: can he see who follows Ahmed?                                                                            | A. No, only the counts; each person sees their own lists. B. Yes, like Instagram.                                                                                                                                                                          | **A.** Less exposure while there's no blocking (M7). **PO: A (2026-11-19): others see only the counts; each person sees and manages only their own Followers and Following lists (including quiet remove-follower). Can be revisited with blocking (M7) or a privacy setting. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Q37 | Block links and phone numbers in comments?                                                                                                                                                                                          | Someone comments "Call me on 98450 12345" or posts a link.                                                                            | A. Block both, with a friendly message. B. Allow both.                                                                                                                                                                                                     | **A.** Stops spam and keeps private numbers off public pages. **PO: A (2026-11-19): comments with a link or a phone number are refused with a friendly inline message so the person can edit and resend; numbers with spaces, dashes or +91 and links with or without http/www are caught; each rule has its own config switch. Number-words and disguised links can slip through; deletion (now) and reporting (M7) are the backstop. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Q38 | How long is a deleted comment's text kept?                                                                                                                                                                                          | Ahmed deletes an abusive comment from Rahul on his deed. Is the text kept for a future review?                                        | A. Comments deleted by their author are erased at once; comments deleted by the doer or reporter are kept 30 days (admins only, used from M7), then erased. B. Always erased at once.                                                                      | **A.** Keeps evidence of abuse briefly without keeping people's words for long. **PO: A, extended (2026-11-19): authors delete only their own comments, erased for good at once; the deed's doer and reporter can delete anyone's comment on their deed (unchanged), text kept 30 days for moderators and admins only, then erased; other members can't delete others' comments. Comment reporting moves into M3 (from M7): members report, moderators and admins remove for policy violations (keep/remove with a note, restore within 30 days), with conflict-of-interest checks; removed text kept 30 days. Reports never hide a comment automatically. PRD §6.5 and ROADMAP updated (D45). Follow-up (2026-11-19): the author of a comment removed by moderation sees a one-time notice in M3, linking to community guidelines (PO-approved copy).**                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Q39 | How is the uploader told the outcome, and for how long?                                                                                                                                                                             | An admin keeps Priya's photo after a report. Does she see "Review complete: your photos stay", and for how long?                      | A. Show the latest outcome on the deed in My deeds for 30 days, with a dot on the deed card until she opens it; "kept" is shown too (as drawn). The "Read the photo guidelines" link opens a short page whose text the PO approves. B. Show only removals. | **A.** Matches the canvas. The guidelines page needs PO-approved copy (we'll draft it). **PO: A (2026-11-19): the uploader sees the latest outcome (removed or kept) on the deed in My deeds for 30 days, with a dot on the deed card until opened; open reports are never shown; the photo-guidelines page copy is drafted by us and approved by the PO before launch. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Q40 | What does a shared link look like in WhatsApp, and can search engines index public pages?                                                                                                                                           | Priya shares Ahmed's deed on WhatsApp. Does the preview show his photo and name? Can Google show the page?                            | A. A generic preview ("A good deed was shared with you · Good Deeds") and no search indexing. B. Preview with the deed's photo, name and text.                                                                                                             | **A.** Previews and search results are copied by other services and can't be taken back when the deed is hidden or a photo removed. **PO: A (2026-11-19): generic link preview (logo and "A good deed was shared with you · Good Deeds"), the same for every deed; public pages are marked noindex; the pre-filled share text is generic too. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Q41 | How are doers told, at M3 launch, that their deeds are now public? (M2 Q21 asked for a reminder at M3 launch.)                                                                                                                      | Ahmed opens Good Deeds after M3 ships.                                                                                                | A. A one-time card at the top of the feed: "Deeds people record about you now appear in the public feed with your name and the area. You can hide any of them from the deed's page." B. No reminder (the My QR line and privacy notice are enough).        | **A.** **PO: A (2026-11-19): a one-time card at the top of the feed for every user, with [See my deeds] and [OK]; seen state kept on the server so it shows once per person. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Q42 | Can signed-in people who haven't finished setting up (no phone yet) use the feed?                                                                                                                                                   | Rahul signed up but hasn't verified his phone.                                                                                        | A. He can read the feed; liking, commenting, saving and following ask him to finish setting up. B. He sees nothing until set up.                                                                                                                           | **A.** Lets new people see what the app is about first. **PO: A (2026-11-19): signed-in users without a verified phone can read the feed, deeds, comments and profiles; liking, commenting, saving, following and reporting comments ask them to finish setting up. Photo reports stay open to anyone (as for visitors) so safety reports never wait. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Q43 | Can people like their own deeds?                                                                                                                                                                                                    | Ahmed likes a deed about himself; Priya likes the deed she reported.                                                                  | A. Yes, once each (counts in Top). B. No; the heart is disabled on your own deeds.                                                                                                                                                                         | **B.** Keeps Top this week a measure of other people's appreciation; it is a small change either way. **PO: A, modified (2026-11-19): the doer and reporter can like their own deed; the like is recorded and included in the displayed like count, but carries zero weight in the Top score (a separate `top_like_count` excludes them). Comments by the doer and reporter are unaffected and count in Top as before. PRD §6.5 updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Q44 | Should "inappropriate or harmful" photo reports be reviewed before ordinary ones? The canvas groups them with "Something else" as "Other".                                                                                          | A photo showing an injured person in distress is reported as harmful.                                                                 | A. Keep the canvas order: children, then privacy, then everything else. B. Put harmful with privacy in the second group.                                                                                                                                   | **B.** Harmful images can hurt people quickly; children still come first. **PO: B (2026-11-19): priority 1 children; 2 privacy and harmful, equal; 3 other. Admin queue chip "Privacy/Harm" (orange) for priority 2, "Other" (grey) only for "something else". Reports never hide a photo automatically; only moderators or admins decide. PRD §6.5 and ROADMAP updated.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

---

## 12. Corrections made to other documents

- **PRD §14:** "S3-compatible object storage (MinIO locally)" → Google Cloud Storage (Mumbai) through the S3-compatible API in production and an S3-compatible container locally (MinIO's image is no longer published), per the 2026-11-18 decision recorded in CLAUDE.md and ROADMAP.
- **PRD §10 step 1:** "if the group already has 3 counted reports" → 5 (the same-act cap is 5: PRD §4, §5, §6.4, §12 and M2 D2).
- **PRD §6.5 and ROADMAP M3:** "Top this week" → one Top tab with a week / month / year dropdown over rolling 7 / 30 / 365-day windows (PO decision Q26, 2026-11-19).
- **PRD §8 Deed:** "media (people helped are blurred or excluded)" → photos, shown as taken, with optional blur by the uploader (PO decision 2026-11-18, PRD §6.5 and §15).
