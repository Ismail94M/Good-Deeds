# M6 Profile Pictures & Notifications: design

**Status:** Revision 2. **All M6A questions answered by the PO on 2026-11-27** (Q86–Q96 and the defaults; decision log in §11); awaiting PO approval to build. Nothing is built yet.
**What changed from Revision 1 (PO direction, 2026-11-27):** M6 is split into two phases.
- **M6A Profile Pictures and In-app Notifications** is designed in full here (§0–§11). It uses **only the local infrastructure we already run**: Postgres, SeaweedFS through `core/storage.py`, and the Procrastinate worker. There is no new cloud service and no new dependency.
- **M6B Cloud Integration** (email, push, production storage) comes after M6A and is only outlined (§12).
- **Monthly challenges** are not in either phase. Their Revision 1 design is parked in §13, and Q96 asks where they should go.

Decisions D103 onwards and questions Q86 onwards are renumbered for this revision. Revision 1's numbers are no longer used. §11 has 11 questions for M6A in three batches; Batch 1 blocks the most design. Obvious defaults are listed at the end of §11 as "needs PO OK".
**Baseline:** `main` at a79d7b3 (M1–M5 merged, migrations up to 0029). Branch `m6-notifications`.
**Sources used:** PRD §1, §6.1, §6.5 (feed, follows, comments, photos, public pages), §6.7, §8 (User, Notification, Follow, Comment), §9, §11 (module table), §15 (DPDP); ROADMAP M1 Deferred, M3, M5, M6; `docs/M2-DESIGN.md` (D8, UserRef rule); `docs/M3-DESIGN.md` (D29–D35, D42–D43, §4.7, comment notices); `docs/M4-DESIGN.md` (D64, D72–D76, SEC3 L6); `docs/M5-DESIGN.md` (D95, D99–D101, §0.3, §10); the code: `core/events.py`, `core/jobs.py`, `core/runtime_config.py`, `core/storage.py`, `core/security.py`, `modules/registry.py`, every `modules/*/events.py`, `identity/models.py` + `service.py` (`public_summaries`, `UserSummary`), `media/` (`models.py`, `service.py`, `_keys.py`, `_limits.py`, `_pipeline.py`, `_worker.py`, `_reports.py`, `_admin.py`), `feed/models.py` (`feed_comments` has no parent: comments are flat; `follows` is one-way), `feed/service.py`, `scoring/_reads.py` ("Unavailable account"), `fraud/_member.py` + `ReviewView`, `tests/test_architecture.py`; frontend `app/Layout.tsx`, `app/routes.tsx`, `features/feed/feedUi.tsx` (`Avatar`, `AvatarLink`, `toneFor`), `features/report/format.ts` (`initials`), `features/feed/useSignedUrlRefresh.ts`, `features/reviews/`, `features/admin/`.

---

## 0. Decisions (M6A)

### 0.0 PO principles that still hold

1. **Encourage first, investigate second.** Notifications celebrate good deeds. Nothing a detector does is ever notified (M5 D98).
2. **Suspicion is not fraud.** No notification ever says or hints "fraud", "flag", "case", "ring", a detector, another person in a case, or an admin's name (M5 D101). Review notifications use neutral text and point to *Profile → Reviews*.
3. **Protect genuine contributors.** People must find out when someone wants their explanation (D112). Likes, comments and follows never change points, ranks or prizes (PRD §6.5, §9).

### 0.1 Architecture decisions

| # | Decision | Why | PRD ref |
| --- | -------- | --- | ------- |
| D103 | **One new module, `notifications`.** Profile pictures are **not** a new module. They live in `media` as a new photo purpose `avatar`, plus one pointer column in `identity`. Registry order: `…, "fraud", "notifications"`. | PRD §11 lists Notifications as a module. Profile pictures are photos, so they get one upload pipeline, one report queue and one set of removal rules. | §11 |
| D104 | **Imports.** `notifications` may import `app.core`, `identity.service` and `deeds.service`. From `media.service` and `scoring.service` it may import **only event classes**. From `fraud.service` it may import **only `MemberReviewUpdate` and `ReviewsSeen`** (both member-safe; `ReviewsSeen` added in the build). From `scoring.service` this includes `AwardWithdrawn` (added after SEC6, so revoked titles and badges read neutrally). From `feed.service` it may import event classes plus two new reads: `visible_deed_ids(session, viewer_id, deed_ids)` and `follower_ids_page(session, user_id, after, limit)`. Nobody imports `notifications`. `identity/handlers.py` may import from `media.service` only `AvatarChanged`. All of this is enforced by allow-lists in `tests/test_architecture.py`, with a test that proves each rule bites. | Keeps the service files acyclic. Keeps fraud details out of member messages by construction: notifications can't even read `FlagRaised` or `FlagCaseResolved`. | §11 |
| D105 | **Only local infrastructure.** M6A uses Postgres, SeaweedFS through `core/storage.py` and the Procrastinate worker. It adds no service and no dependency: Pillow is already used by the media pipeline. **No email, no push, no service worker, no manifest.** A notification row doesn't depend on how it is delivered, so M6B can add delivery channels beside it without changing M6A tables (§12). | PO direction 2026-11-27. M6A is cheap to run locally and needs no accounts or secrets. | ROADMAP M6 |
| D106 | **A notification is a row per recipient, written by an event handler, holding ids and numbers only.** Handlers subscribe through the existing outbox (`@subscribe`, `core.handle_event`, `processed_events`), so each event creates its notification exactly once. The text is built **when the list is read**, from live data (`deeds.service`, `identity.public_summaries`, `feed.service.visible_deed_ids`). So a hidden or withdrawn deed, a changed name, or an "Unavailable account" shows correctly in old notifications, and no deed text is copied into a new table. | The same "project, then re-check live" pattern as `feed_posts` (M3 D37). With no copied personal text, there is nothing extra to erase when an account is deleted. | §6.7 |
| D107 | **A fixed catalogue of kinds (§4.1), in five categories:** *About you* (recognitions of you, your deed verified), *Your network* (people you follow recognising or being recognised), *Activity* (likes, comments, replies, new followers), *Account & safety* (moderation outcomes, badges, titles), and *Reviews*. Texts and links live in code. Which categories can be switched off, plus the caps, live in config. | A small list the PO can approve word by word. Anything tunable is config. | §6.7 |
| D108 | **Grouping keeps the list short, like Instagram.** While a row is unread, new activity of the same kind is folded into it: the newest 3 actors are kept, the count goes up, and `updated_at` moves it to the top. Once the row is read, the next activity starts a new row. Groups: likes **per deed**; comments **per deed**; replies **per deed**; new followers **per day**; network recognitions **per day** (D110). A person counts once per group (unliking and liking again doesn't count twice; `notification_dedupe`, 30 days). | Avoids notification storms. | §6.7 |
| D109 | **Who is never notified.** Nobody is notified about their own action. Inactive recipients (suspended, blocked) get nothing. A pending invite deed never names its doer, so it creates no notification. Network and activity rows are shown only while the deed is visible in the feed (re-checked on read). Otherwise the row reads "This deed is no longer available" with no link. An inactive actor shows as "Unavailable account" with the neutral default avatar, as on leaderboards (M4 SEC3 L6). Mutes apply (D113). | The M2/M3 privacy rules apply to every new surface. | §6.5 |
| D110 | **Network fan-out ("your friends recognised a good deed").** The app has no friends, only one-way follows (PRD §6.5). When a deed is verified, people connected to its reporter or doer get **one grouped row per day**: "Priya, Ravi and 3 others you follow shared good deeds today". The audience is decided by Q89; the draft uses people who follow the reporter or the doer, the same people as the Following tab. The roles are the same as the Following tab: the followed person reported the deed or did it. The `DeedVerified` handler only defers a job, `notifications.fanout_network`. The job pages through `feed.service.follower_ids_page` in batches of `network.fanout_batch` (1,000), resumes after a restart, and skips people who are inactive, have muted the actor, have switched *Your network* off, or can't see the deed. **Limits:** at most `network.max_fanouts_per_actor_day` (3) deeds per actor per day start a fan-out; at most `network.max_recipients_per_event` (5,000) recipients per deed; one row per recipient per day. The deed's own doer and reporter get their *About you* rows instead. | PO requirement. Grouping by day and capping per actor stops a busy reporter or a popular account from flooding thousands of bells. Stories (D73) already show the same deeds for 24 h, so the row links to Home, where the Stories rings are. | §6.5 Following |
| D111 | **"Replies" today.** Comments are flat: `feed_comments` has no parent or reply field. A **reply** is therefore "someone commented on a deed **after you commented on it**": "Ahmed and 2 others also commented on the deed you commented on." It goes to earlier commenters (not deleted, in the last `replies.lookback_days`, 30), grouped per deed, and never to the deed's doer or reporter (they get the comment row). A per-deed mute "Turn off notifications for this deed" stops it. Threaded replies would be a feed change and are not part of M6A (Q90). | Facebook-style "also commented" works without changing comments. Rows are grouped, can be muted and expire. | §6.5 comments |
| D112 | **Reviews (M5) notify in-app, with neutral text.** `fraud` emits a new member-safe event `MemberReviewUpdate(user_id, kind, item_id, reply_due_at?)`. `kind` is `explanation_requested`, `explanation_reminder`, `decision_notice`, `appeal_decided` or `review_closed`, and `item_id` is the member's own request or notice id. It carries **no case id, case type, detector, other person or admin**. `notifications` turns it into kind `review_update`: "There's an update for you in Reviews". For an explanation request it says "We'd like to hear from you about some of your recent deeds. Please reply by {date}." It links to `/me/reviews`. Which of the five kinds notify is Q91 (draft: all five). A new daily job `fraud.remind_explanations` emits one reminder `explanation.reminder_days_before_due` (3) days before the reply is due, if there is still no reply. The M5 Profile dot and `POST /api/reviews/seen` stay. Opening Reviews also marks `review_update` rows read. `notifications` never subscribes to `FlagRaised`, `FlagCaseResolved`, `DeedHoldChanged`, `FlagDecisionFinal`, `HeroTitleRevoked` (it carries `case_id`) or `PointsChanged`, and a test proves it. | Closes M5 §0.3/§10 for the in-app channel without breaking D101. Principle 3: the explanation step only protects people who know about it. | M5 D99–D101 |
| D113 | **Preferences and mutes.** Each category can be switched on or off, except *Account & safety* and *Reviews*, which are always on. **Mute a person** (from their profile "⋯" or a notification's "⋯"): stops *Activity* and *Your network* rows caused by them. Recognitions of you still arrive, because they are about your own deeds. **Mute a deed:** stops likes, comments and replies on that deed. Mutes are silent: the other person isn't told. Unmute from the settings page. | Avoids excessive notifications without blocking (blocking is M7). | §6.7 |
| D114 | **The bell.** The header gets a bell with a count from `GET /api/notifications/unread-count`. It is fetched on page load, on window focus, and every `unread_poll_seconds` (60) while the tab is visible. No sockets. Opening the list sets `notification_views.seen_at`, and the number shows new rows since then, like Instagram. Each row can be marked read or unread; "Mark all as read" reads everything. Tapping a row marks it read and opens its link. | The API stays stateless (PRD §11). 60 s is fine for good-deed news. | §11 |
| D115 | **Caps and retention.** Per recipient and day: at most `daily_caps.activity` (30) new *Activity* rows. Beyond that, groups keep growing but no new rows are created. At most 1 *Your network* row. *About you*, *Account & safety* and *Reviews* have no cap. Rows are deleted `retention_days` (90) after their last update, and dedupe rows after 30 days (nightly `notifications.purge`). When account deletion is built (M2 D8), it must emit `identity.UserAnonymised`, and `notifications` deletes that person's rows, preferences and mutes. | No floods. Data minimisation (DPDP). | §15 |
| D116 | **Profile pictures reuse the M3 media pipeline on local storage.** A new purpose, `photos.purpose = 'avatar'`, gets the same presigned PUT to SeaweedFS `staging/` (`POST /api/media/uploads`), `complete` (HEAD + magic bytes; declared type and size checked against `upload_allowed_types` / `upload_max_bytes`), worker decode → validate (pixel limit, no animation) → orient → colour, WebP re-encode (no EXIF, GPS or other metadata copied), raw upload deleted, signed GET URLs, `held/` moves on admin removal, and the purge jobs. New pieces: a server-side **square crop** step (the client sends a normalised square, and pixels outside it are never stored), **no blur** step, and square variants `avatar_sm` 96 px, `avatar_md` 256 px and `avatar_lg` 512 px. There is one live picture per person (partial unique index). **Replace:** a new picture goes live only when it is ready, and the old one is then deleted at once. | PO requirement and M3 D32–D35: one pipeline, one set of security checks. The crop is server-side for the same reason the blur is (D33): what the client cuts away must not exist on our side. | §6.5 photos |
| D117 | **Who holds the pointer, and how URLs are made.** On every change, `media` emits `AvatarChanged(user_id, avatar_ref \| null, changed_at)`. `identity` stores it in `users.avatar_ref`, with `avatar_changed_at` so an older event never overwrites a newer one. `identity.public_summaries` returns `avatar_ref`, so every `UserRef` in every module gains **`avatar: string \| null`**. It is an opaque reference, never a URL. The browser swaps references for signed URLs with **one batched call** per screen: `POST /api/media/avatars/urls {refs}` (signed in, up to 100 refs, deduplicated and cached on the client until `expires_at`). Media signs only refs that are still the owner's live picture on an active account. | Scoring and fraud may not import `media` (the M4/M5 boundary tests), so they can't sign URLs. A reference coming from identity keeps every module boundary as it is. Only media creates GET URLs (M3 D34). | §11 |
| D118 | **Avatar URLs live longer than deed-photo URLs.** `avatar_url_ttl_minutes` is 1440 and `avatar_url_step_minutes` is 720 (deed photos: 60/30). Variants carry `Cache-Control: private, max-age=86400`. This is safe because every removal (by the owner or an admin) **moves or deletes the objects**, so links already handed out stop working at once (M3 §4.7). | Long lists (leaderboards, comments, followers) show the same faces again and again. A URL that stays the same for 12 h means each picture downloads about once a day. | §6.5 |
| D119 | **Who sees a profile picture (Q86): signed-in members only.** Public pages (the shared deed page `/d/:id`, the signed-out view of `/u/:handle`) always show the default avatar. Their responses carry `avatar: null`, and the URL endpoint needs `CurrentUser`. | PRD §6.5: "Public pages show no … personal information". A face is personal data. | §6.5, §15 |
| D120 | **Default avatars (Q88).** Draft: the **generated initials on a soft colour** that is built today (`initials()` + `toneFor(handle)`), so a person keeps the same colour everywhere. They show whenever there is no picture, while a picture loads, if it fails to load, and on public pages. "Unavailable account" rows and deleted users get a **neutral silhouette** instead, so no initials of a hidden name are shown. | Already built, recognisable and costs no assets or storage. The silhouette avoids leaking initials where the name is hidden. | – |
| D121 | **Reporting and moderation (Q87) reuse photo reports.** Signed-in, onboarded members can report a profile picture from the profile "⋯" menu. It becomes a normal `photo_report_cases` row in the existing queue, under the filter "Profile pictures". Reasons: "Shows a child", "Not this person / impersonation" (new `impersonation`), "Private details", "Inappropriate or harmful", "Other". Priority follows Q44 (children first). A report never hides the picture by itself (M3 D42). Moderators and admins decide keep, remove or restore (M3 Q34), and nobody decides a report on their own picture. Admin removal holds the picture privately for `removed_photo_retention_days` (90) so it can be restored, and the owner gets an *Account & safety* notification (Q95). | No second queue and no second set of rules. | §6.5 reporting |
| D122 | **Account states and deletion.** A suspended or blocked account's picture is not signed: it gets `avatar: null` and the silhouette everywhere. M7 blocking must also move the objects to `held/`. "Unavailable account" rows (revoked titles, M5 Q72; inactive users, SEC3 L6) always have `avatar: null`. The owner can remove their picture at any time, and it is deleted at once. When account deletion is built, `identity.UserAnonymised` must trigger the same removal in `media`. | Removing the name but leaving the face would undo anonymisation. | §15 |
| D123 | **Picture rules.** A profile picture shows the account holder, or any image with no other identifiable person in it. No children (Q94). No contact details, QR codes or phone numbers. The photo-guidelines page gains a "Profile pictures" section (copy for PO approval). Changes are rate-limited to 5 a day. | PRD §15 (DPDP, children), M3 Q44. Impersonation would undermine "Verified by a witness". | §6.5, §15 |
| D124 | **Pictures everywhere a person is named**, including admin screens (PO 2026-11-27): feed cards, the deed page, comments, Stories, the profile header, connections, leaderboards and Featured City, notifications, *Profile → Reviews* (the other person on each of my own deeds), the matched doer on Record a deed, the header, and the admin queues, case pages, appeals and the audit actor. M5 D101 still holds, because avatars appear only where a `UserRef` already names a person, so no screen reveals anyone new. | Consistency (PO requirement). On admin screens a picture also helps spot impersonation. The bias risk on fraud reviews is noted for the security review (§9). | M5 D101 |
| D125 | **Config namespaces, all versioned rows with no defaults:** new `notifications` v1 (`notifications/_limits.py:NotificationsConfig`), a new version of `media` (avatar keys), and a new version of `fraud` (`explanation.reminder_days_before_due`). Each new version is a full copy of the latest values plus the new keys, published by its migration. M6A adds no `GD_*` settings. | CLAUDE.md config rule. | §12 |

### 0.2 PRD rules that are ambiguous or conflict with built behaviour

| # | PRD rule | Problem | Resolution in this draft |
| --- | -------- | ------- | ------------------------ |
| A21 | §6.7 "Your deed was verified" | With QR scans, the reporter's deed is verified the moment they send it. Only invite deeds (switched off, M2 D17) are verified later. | The doer gets "You were recognised" for every verified deed. The reporter gets "Your deed was verified" only for invite deeds (dormant while invites are off). |
| A22 | PO "friends and followers recognise good deeds" | There are no friends, only one-way follows (§6.5). | D110; the audience is Q89. |
| A23 | PO "replies" | Comments are flat (no threads). | D111 "also commented"; Q90. |
| A24 | §6.7 "challenge and prize updates" | Challenges are parked and prizes are M8. | Not in M6A; Q96 for challenges. |
| A25 | §8 User; §6.5 public pages; the M2 rule that UserRef carries "never … avatar URL" | The PRD has no profile picture. | Add `avatar` to the conceptual User. `UserRef` gains an opaque `avatar` ref (not a URL), never on public pages (D117, D119). The DOC task updates the PRD. |
| A26 | §6.1 "email the owner on every reset" | Needs email. | Moved to M6B (§12). M6A adds an in-app *Account & safety* row "Your password was changed" (`PasswordChanged` event). This is a useful record, but it is no protection against a SIM swap. |
| A27 | §10 step 5 "Points → leaderboard → notifications" | Reads as if notifications wait for points. | Notifications subscribe to the deed events directly. Points changes are never notified (provisional, and M5 D95 `PointsChanged(notify=false)`). |

### 0.3 Out of scope for M6A

- Email, push, a service worker, a web app manifest, email verification, the password-changed email, GCS (all M6B, §12).
- Monthly challenges (parked, §13; Q96).
- SMS notifications, digests, sockets or server-sent events.
- Threaded comment replies (Q90), @mentions.
- Notifying points changes, deed hides, or withdrawals (Q93).
- Prize notifications (M8). Blocking (M7).
- AI checks of pictures (M7 AI pre-screen), cover photos, video avatars.

---

## 1. Scope (M6A)

**In:**
- **Profile pictures:** upload, crop, preview, replace and remove on mobile and desktop (D116). Pictures show everywhere a person is named (D124), with default avatars (D120), reports and moderation in the existing queue (D121), and the account-state rules (D122).
- **In-app notifications:** a bell with a count, history, tap-through, read/unread for one row and for all, preferences and mutes (D113–D114). Kinds in §4.1: recognitions of you, your network (D110), likes, comments and replies (grouped), new followers, badges and titles, photo, comment and avatar moderation outcomes, password changed (in-app), and review updates (D112). Also grouping, caps and retention (D108, D115).

**Out:** §0.3.

---

## 2. Data model (database-specialist)

Migrations (all new; no existing migration is edited): **0030 `notifications`**, **0031 `media_avatars`**, **0032 `identity_avatar`**, **0033 `fraud_review_reminders`**. FKs to `users` and `deeds` use `RESTRICT` unless noted.

### 2.1 `notifications` (migration 0030)

**`notifications`**

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | uuid PK | |
| user_id | uuid FK users | recipient |
| kind | varchar(40) | from the catalogue (§4.1); CHECK against the list |
| category | varchar(20) | `about_you`, `network`, `activity`, `account`, `reviews` |
| actor_ids | uuid[] | newest first, at most 3; empty for system rows |
| actor_count | int | distinct actors in the group |
| deed_id | uuid null FK deeds | |
| ref_id | uuid null | the other object (comment, photo, badge award, title, review item); no FK |
| data | jsonb | ids, codes and numbers only (`badge_slug`, `period_month`, `level`, `review_kind`, `reply_due_at`, `day`) |
| dedupe_key | varchar(200) null | e.g. `recognised:{deed_id}`; makes a re-run harmless |
| group_key | varchar(200) null | `like:{deed_id}`, `comment:{deed_id}`, `reply:{deed_id}`, `follow:{day}`, `network:{day}` |
| created_at, updated_at | timestamptz | |
| read_at | timestamptz null | |

Constraints: `UNIQUE (user_id, dedupe_key)`; partial unique `(user_id, group_key) WHERE read_at IS NULL AND group_key IS NOT NULL`. Indexes: `(user_id, updated_at DESC, id)` (list, keyset); partial `(user_id) WHERE read_at IS NULL` (count).

**`notification_views`**: `user_id` PK FK users, `seen_at` (bell opened). Upserted, like `review_views`.

**`notification_preferences`**: `user_id` FK, `category` varchar(20), `enabled` bool, `updated_at`; PK `(user_id, category)`. A missing row means the config default. The service refuses to switch off a locked category.

**`notification_mutes`**: `user_id` FK, `target_type` enum `person`, `deed`, `target_id` uuid, `created_at`; PK `(user_id, target_type, target_id)`. At most `max_mutes` (500) per person.

**`notification_dedupe`**: `user_id`, `key` varchar(200), `created_at`; PK `(user_id, key)`. Used for (group, actor) pairs and the per-actor daily fan-out counter key. Purged after `dedupe_days`.

**`notification_fanouts`**: `deed_id` PK, `actor_ids` uuid[], `cursor` uuid null (last follower id done), `recipients` int, `status` enum `running`, `done`, `capped`, `created_at`, `finished_at`. Lets `fanout_network` resume and keeps a record of when a cap was hit.

**`notification_daily_counts`**: `user_id`, `day` date (IST), `category`, `count` smallint; PK `(user_id, day, category)`. Used for the caps in D115. Purged after 7 days.

### 2.2 `media` (migration 0031)

- `photo_purpose` gains `avatar`. The enums are not native Postgres enums, so the migration replaces their CHECK constraints.
- `photos.avatar_crop` jsonb null: `{x, y, size}` normalised 0–1 on the oriented image. CHECK: it is set exactly when `purpose = 'avatar'`.
- `ck_photos_owner_matches_purpose`: `avatar` rows have neither `deed_id` nor `self_log_id`.
- `ck_photos_takedown_deed_only` becomes `takedown = 'none' OR purpose IN ('deed', 'avatar')`. A new CHECK keeps `doer_removed` deed-only.
- `uq_photos_live_avatar UNIQUE (uploader_user_id) WHERE purpose = 'avatar' AND status = 'ready' AND takedown = 'none'`.
- `photo_report_reason` gains `impersonation` (priority 2).
- Keys reuse `_keys.variant_key` (`photos/{id}/avatar_md-1.webp`), so `held/` moves and purges work unchanged.
- Publishes `media` v2 (§2.5).

### 2.3 `identity` (migration 0032)

`users.avatar_ref` varchar(80) null, `users.avatar_changed_at` timestamptz null.

### 2.4 `fraud` (migration 0033)

**`explanation_reminders`**: `request_id` PK FK explanation_requests, `sent_at`. Insert-only (trigger), so the reminder fires once. The M5 trigger on `explanation_requests` is not touched. Publishes the next `fraud` version with `explanation.reminder_days_before_due: 3` (validation: 1 ≤ value < `reply_days`).

### 2.5 Config

**`notifications` v1** (`NotificationsConfig`, no defaults):

```json
{
  "timezone": "Asia/Kolkata",
  "categories": {
    "about_you": { "default": true, "locked": false },
    "network":   { "default": true, "locked": false },
    "activity":  { "default": true, "locked": false },
    "account":   { "default": true, "locked": true },
    "reviews":   { "default": true, "locked": true }
  },
  "review_kinds": ["explanation_requested", "explanation_reminder", "decision_notice",
                   "appeal_decided", "review_closed"],
  "network": { "audience": "followers_of_actor", "roles": ["reporter", "doer"],
               "fanout_batch": 1000, "max_recipients_per_event": 5000,
               "max_fanouts_per_actor_day": 3 },
  "replies": { "enabled": true, "lookback_days": 30 },
  "daily_caps": { "activity": 30, "network": 1 },
  "actors_kept": 3,
  "dedupe_days": 30, "retention_days": 90, "max_mutes": 500,
  "page_size": 30, "unread_poll_seconds": 60,
  "rate_read": "300/60", "rate_write": "60/60"
}
```

`network.audience` is one of `followers_of_actor`, `mutual`, `followers_of_actor_or_followed_by` (Q89). Validation: every category is present; `account` and `reviews` are locked; the caps are ≥ 1; `review_kinds` is a subset of the five kinds.

**`media` v2** = v1 plus `{"avatars_enabled": true, "avatar_variants": [{"name": "avatar_sm", "max_side_px": 96}, {"name": "avatar_md", "max_side_px": 256}, {"name": "avatar_lg", "max_side_px": 512}], "avatar_min_side_px": 256, "avatar_url_ttl_minutes": 1440, "avatar_url_step_minutes": 720, "avatar_sign_max_refs": 100, "avatar_reupload_cooldown_hours": 24, "rate_avatar_change_user": "5/86400", "rate_avatar_sign_user": "120/60"}`. Validation: the step is less than the TTL, and all three avatar variant names are present. Upload type and size reuse `upload_allowed_types` and `upload_max_bytes`.

---

## 3. API contract

### 3.1 Conventions and service additions

Thin routers, `AppError` in services, opaque keyset cursors, and rate limits per account from config. Own data only: another person's notification or mute is 404. `UserRef` everywhere becomes `{handle, display_name, avatar}`, where `avatar` is an opaque string or null.

- `identity.service`: `UserSummary.avatar_ref`. `public_summaries` returns it, null for inactive users. Re-exports `PasswordChanged` (emitted by `reset_password`) and `UserAnonymised` (the class only; it gets an emitter when account deletion is built). `Me` gains `avatar`.
- `media.service`: events `AvatarChanged` and `AvatarModerationDecided`; `avatar_urls(session, refs)`.
- `feed.service`: `visible_deed_ids(session, viewer_id, deed_ids) -> set`, `follower_ids_page(session, user_id, *, after, limit) -> list[uuid]`, `earlier_commenter_ids(session, deed_id, *, before, since) -> set` (for D111).
- `fraud.service`: `MemberReviewUpdate`.
- Every module's own `UserRef` schema (deeds, feed, scoring, fraud, media admin) gains `avatar`. Scoring's "Unavailable account" rows and fraud's `_GONE` get `avatar: null`. Public endpoints (`/api/public/*`, signed-out profile) force null.

### 3.2 Notifications

| # | Method, path | Request | Response | Errors |
| - | ------------ | ------- | -------- | ------ |
| N1 | `GET /api/notifications?cursor=&unread=` | | `{items: [{id, kind, category, created_at, updated_at, read: bool, text, actors: UserRef[], actor_count, link: {kind: "deed"\|"my_deed"\|"profile"\|"reviews"\|"home"\|"badges"\|"guidelines"\|"connections"\|"settings", target}\|null, can_mute: {person?: handle, deed?: id}}], next_cursor}`, newest activity first. `text` is built on read (D106) | 400 `invalid_cursor` |
| N2 | `GET /api/notifications/unread-count` | | `{unread, new_since_seen}` (the bell shows `new_since_seen`) | |
| N3 | `POST /api/notifications/seen` | | 204 | |
| N4 | `POST /api/notifications/{id}/read` · `/unread` · `POST /api/notifications/read-all` | | 204 | 404 |
| N5 | `GET /api/notifications/preferences` | | `{categories: [{id, label, description, on, locked}], mutes: {people: [UserRef], deeds: [{deed_id, title_text}]}}` | |
| N6 | `PUT /api/notifications/preferences` | `{category, on}` | the N5 body | 409 `category_locked`; 422 `unknown_category` |
| N7 | `PUT /api/notifications/mutes` · `DELETE` | `{type: "person"\|"deed", target}` (handle or deed id) | 204 | 404 (unknown, or a deed you can't see); 409 `too_many_mutes` |

All of N1–N7 need `CurrentUser`. Reads use `rate_read` and writes use `rate_write`.

### 3.3 Media additions (profile pictures)

| # | Method, path | Request | Response | Errors |
| - | ------------ | ------- | -------- | ------ |
| P1 | `POST /api/media/uploads` (existing) | gains `purpose: "deed"\|"avatar"` (default `deed`) | as today (presigned PUT to SeaweedFS) | 409 `avatars_disabled`; 413/422 as today; 429 |
| P2 | `PUT /api/media/me/avatar` | `{photo_id, crop: {x, y, size}}` (after `complete`) | 202 `{status: "processing"}` | 409 `upload_not_complete`, `cooldown`; 422 `crop_invalid`, `image_too_small`; 429 |
| P3 | `GET /api/media/me/avatar` | | `{current: AvatarUrls\|null, pending: {status, reject_reason?}\|null, removed_notice: {removed_at, reason_label}\|null}` | |
| P4 | `DELETE /api/media/me/avatar` | | 204 (deleted at once) | |
| P5 | `POST /api/media/avatars/urls` | `{refs: string[]}` (≤ `avatar_sign_max_refs`) | `{items: {[ref]: {sm_url, md_url, lg_url, expires_at}\|null}}`; null when a ref isn't live or its owner is inactive | 401; 422 `too_many_refs`; 429 |
| P6 | `GET /api/users/{handle}/avatar-report-target` → `POST /api/media/photos/{photo_id}/reports` (existing) | reason, details | 202 | 404; 429. Onboarded members only |

The admin photo-report queue (`GET /api/admin/photo-reports`) gains a `purpose` filter and a `purpose` field on rows. For an avatar, the detail shows the owner instead of a deed. Decisions are unchanged. Restore answers 409 `avatar_replaced` when the owner has a newer picture.

---

## 4. Rules and processing

### 4.1 Notification catalogue (copy _interim_, for PO review)

| Kind | Category | From event | To | Text (built on read) | Group |
| ---- | -------- | ---------- | -- | -------------------- | ----- |
| `recognised` | about_you | `DeedVerified` | doer | "{reporter} recognised you for a good deed: {category}." → deed | none |
| `deed_verified` | about_you | `DeedVerified` (`invite_accept` only) | reporter | "Your deed about {doer} was verified." | none |
| `network` | network | `DeedVerified` → `fanout_network` | Q89 audience | "{a}, {b} and {n} others you follow shared good deeds today." → Home | per day |
| `like` | activity | `DeedLiked` | doer, reporter (not the liker) | "{a}, {b} and {n} others liked the deed about you / you recognised." | per deed |
| `comment` | activity | `CommentPosted` | doer, reporter (not the author) | "{a} and {n} others commented on the deed about you." | per deed |
| `reply` | activity | `CommentPosted` | earlier commenters (D111) | "{a} and {n} others also commented on a deed you commented on." | per deed |
| `follow` | activity | `UserFollowed` | followed | "{a}, {b} and {n} others started following you." → connections | per day |
| `badge_awarded` | account | `BadgeAwarded` | owner | "You earned the {badge} badge." → profile medals | none |
| `title_awarded` | account | `HeroTitleAwarded` | owner | "You're Kindness Hero of {area} for {month}!" / "Top 3 in Bangalore for {month}" | none |
| `photo_removed` / `photo_kept` | account | `PhotoModerationDecided` | uploader | M3 My deeds outcome text → `/me/deeds/:id` | none |
| `photo_removed_by_doer` | account | `PhotoRemovedByDoer` | uploader | "{doer} removed 1 photo from this deed." (M3 neutral notice) | none |
| `comment_removed` | account | `CommentModerationDecided` (`remove`) | author | the M3 notice, link to the community guidelines | none |
| `avatar_removed` | account | `AvatarModerationDecided` (`remove`) | owner | "Your profile picture was removed because it didn't follow our photo guidelines." → guidelines | none |
| `password_changed` | account | `PasswordChanged` | owner | "Your password was changed. If this wasn't you, contact support." | none |
| `review_update` | reviews | `MemberReviewUpdate` | owner | D112 texts → `/me/reviews` | none |

The M3 in-app notices (photo outcomes in My deeds, comment notices) stay where they are; the notification links to them.

### 4.2 Writing (`notifications/_write.py: notify`)

All of this happens in the handler's transaction:
1. Skip if the recipient is the actor, is inactive, has the category off, or has muted the actor or deed (D113).
2. For a grouped kind: check `notification_dedupe` for (group, actor). Then upsert the unread row for `(user_id, group_key)`: put the actor first, keep `actors_kept`, add 1 to the count, move `updated_at`.
3. Otherwise insert with `ON CONFLICT (user_id, dedupe_key) DO NOTHING`.
4. A new *Activity* row first checks and increments `notification_daily_counts`. Over the cap, no row is created, but existing groups still grow.

Network rows: `fanout_network(deed_id)` (job, queue `notifications`):
1. Check the per-actor daily counter and skip if it is over.
2. Page through the followers of the reporter and the doer (deduplicated), `fanout_batch` per transaction, saving the cursor in `notification_fanouts`.
3. Run each recipient through steps 1–2 with `group_key = network:{IST day}`.
4. Stop at `max_recipients_per_event` (status `capped`).

### 4.3 Profile pictures (`media/_avatars.py`, `_pipeline.py`)

- Upload as today with `purpose = avatar`. `PUT /me/avatar` checks the crop (square in pixels after orientation, side ≥ `avatar_min_side_px`) and queues processing.
- Pipeline for avatars: decode → validate → orient → colour → **crop** → derive square variants → encode WebP (metadata not copied; a test checks EXIF and GPS are gone) → raw upload deleted. A rejection (wrong type, too large, animated, corrupt) shows on P3 as `pending.reject_reason`.
- On ready, in one transaction: lock the owner's live avatar row, mark it `deleted` (the existing `purge_deleted` job removes its objects), make the new one live, and emit `AvatarChanged(new ref)`.
- `avatar_ref` = `{photo_id}-{key_version}`, opaque to clients. A restore makes a new version and so a new ref.
- **Owner removal:** `deleted`, objects deleted at once, `AvatarChanged(null)`.
- **Admin removal:** `takedown = admin_removed` and `object_move = hold` (existing mover), `AvatarChanged(null)`, `AvatarModerationDecided`.
- **Restore:** the existing release move, then `AvatarChanged(ref)` only if no newer picture is live.
- `avatar_urls`: one query from refs to the live rows of active owners (`identity.service.active_user_ids`), signed with the avatar TTL and step.

### 4.4 Identity handler

`identity/handlers.py: on_avatar_changed` sets `avatar_ref` and `avatar_changed_at` only when `event.changed_at >= users.avatar_changed_at` (or that is null). Out-of-order events are therefore harmless.

---

## 5. Events

**New:**

| Event | `event_type` | Emitted by | Payload | Subscribers |
| ----- | ------------ | ---------- | ------- | ----------- |
| `PasswordChanged` | `identity.password_changed` | `identity.reset_password` | `user_id, method` | notifications (M6B adds email) |
| `UserAnonymised` | `identity.user_anonymised` | (class only until account deletion) | `user_id` | media, notifications (when built) |
| `AvatarChanged` | `media.avatar_changed` | media (ready, owner removal, admin removal, restore) | `user_id, avatar_ref\|null, changed_at, reason` | identity |
| `AvatarModerationDecided` | `media.avatar_moderation_decided` | media admin decision on an avatar | `photo_id, case_id, user_id, action, removal_reason` | notifications |
| `MemberReviewUpdate` | `fraud.member_review_update` | fraud: explanation request, reminder, decision notice, appeal decision, look finished | `user_id, kind, item_id, reply_due_at?` | notifications |

**Existing events gaining a subscriber (notifications):** `DeedVerified`, `DeedLiked`, `CommentPosted`, `UserFollowed`, `CommentModerationDecided`, `PhotoModerationDecided`, `PhotoRemovedByDoer`, `BadgeAwarded`, `HeroTitleAwarded`. Avatar decisions emit `AvatarModerationDecided` rather than `PhotoModerationDecided`, whose `deed_id` is required, so existing subscribers don't change. Payloads carry ids, codes and numbers only. The notification handlers are **not** added to the M4/M5 finalization barrier.

**Jobs:** `notifications.fanout_network` (deferred, queue `notifications`), `notifications.purge` (nightly 03:40 IST), `fraud.remind_explanations` (daily 10:00 IST). The existing media jobs (`process_queued`, `move_objects`, `purge_deleted`, …) handle avatars unchanged.

---

## 6. UI flows (frontend-developer)

### 6.1 Files and routes

- `src/features/notifications/`: `notificationsApi.ts`, `NotificationBell.tsx` (header), `NotificationsPage.tsx` (`/notifications`, tabs All / Unread), `NotificationSettingsPage.tsx` (`/settings/notifications`), `MuteMenu.tsx`.
- `src/features/media/avatar/`: `useAvatarUrls.ts` (a batching loader: every ref asked for in one tick goes into one `POST /media/avatars/urls`; each ref is cached in TanStack Query until `expires_at` minus 5 min; `useSignedUrlRefresh` is reused on image error), `AvatarEditor.tsx`, `DefaultAvatar.tsx`.
- `src/features/profile/`: "Add photo" / "Change photo" on `ProfilePage`; "⋯ → Report profile picture" and "Mute notifications from {name}" on `PublicProfilePage`.
- `src/features/admin/`: a "Profile pictures" filter on the photo-report queue, plus the avatar case detail.

### 6.2 The `Avatar` component and every place it is used

`feedUi.tsx` `Avatar` / `AvatarLink` gain an `avatar?: string | null` prop and an `unavailable?: boolean` prop. They always render the default (D120: initials on `toneFor(handle)`, or the silhouette for unavailable accounts) and lay `<img alt="" loading="lazy" decoding="async">` over it once the URL arrives. On error they refresh once, and then the default stays. The size picks the variant: `sm`/`md`/`lg` → `avatar_sm`/`avatar_md`, `xl`/`story` → `avatar_lg`. `ProfilePage.tsx` and `DeedForm.tsx`, which call `initials()` directly today, switch to `Avatar`.

Call sites (all already have a `UserRef`): PostCard doer (`PublicPostCard`: always the default), DeedPage, CommentsSection (each author, plus my own composer avatar from `me`), StoriesBar, StoryViewer, ConnectionsPage, PublicProfilePage (xl; the default when signed out), ProfilePage (mine, with the edit button), DeedForm (matched doer), LeaderboardPage rows, FeaturedCity podium and list, NotificationsPage actors (up to 2, stacked), ReviewsPage (`other_person`), and **the header** (my small avatar before "Profile", keeping the M5 dot). Admin screens: PhotoReport and CommentReport queues and reviews, FraudCases, FraudCaseReview (people, edges, deeds), Appeals, AppealReview, Override requests, UserScoringAdmin, AuditLog actor.

### 6.3 Screens and states (copy _interim_)

- **Avatar editor** (a bottom sheet on phones, a dialog on desktop):
  1. "Add a profile photo. Use a photo of you, or any picture without other people. No children."
  2. [Take photo] (phones, `capture="user"`) / [Choose photo].
  3. Crop: a square frame you drag, with a zoom slider. Works with touch, mouse and keyboard (arrow keys move, +/− zoom). A live **preview** shows the picture in a circle at three sizes, as it will appear.
  4. [Save] → "Uploading…" → "Processing…" → done (the profile updates).
  5. Errors: "This file type isn't supported", "This photo is too large", "This photo is too small: pick one at least 256 × 256", "You've changed your photo a lot today; try tomorrow", "You can add a new photo after {time}" (cooldown).
  6. [Remove photo], with a confirm. A link to the photo guidelines.
- **Bell** (header, beside "Record a deed"): an icon with a count up to "9+". Tap → `/notifications`. Screen reader: "Notifications, 3 new".
- **Notifications page:** All / Unread tabs. Unread rows are tinted with a dot and show the actors' avatars, the text and a relative time. Tapping opens the link and marks the row read. Each row's "⋯" offers Mark as read/unread, Mute {name}, and Turn off notifications for this deed. "Mark all as read" at the top. Empty: "Nothing yet. When someone recognises you, you'll see it here." Loading and error states. Infinite scroll.
- **Notification settings:** one switch per category with a one-line description. Locked categories show "Always on, so you never miss something about your account" / "…a review". Below: muted people and muted deeds, each with [Unmute].
- **Profile picture report:** profile "⋯" → "Report profile picture" → reasons → "Thanks. Someone from our team will look at it."

### 6.4 Frontend rules

Texts come from the server (N1); the client computes no rules. Query keys: `['notifications', 'list', tab]`, `['notifications', 'unread']`, `['notifications', 'prefs']`, `['avatar', ref]`, `['me', 'avatar']`. After a change, invalidate `['me']`.

---

## 7. Task list (M6A)

Agents: **DB**, **BE**, **FE**, **QA**, **SEC**, **AD**, **DOC** (main session). Work starts after the PO answers Batch 1.

| ID | Owner | Task | Depends on | Parallel |
| -- | ----- | ---- | ---------- | -------- |
| DB12 | DB | Migration 0030 `notifications` (§2.1) + `notifications` v1 | – | with DB13–DB15 |
| DB13 | DB | Migration 0031 `media_avatars` (§2.2) + `media` v2 | – | |
| DB14 | DB | Migration 0032 `identity_avatar` (§2.3) | – | |
| DB15 | DB | Migration 0033 `fraud_review_reminders` (§2.4) + `fraud` next version | – | |
| B46 | BE | Architecture tests first: the D104 allow-lists, notifications never imports fraud except `MemberReviewUpdate`, never subscribes to the listed fraud events, plus a test that each rule bites | – | with B47 |
| B47 | BE | Media avatars: purpose, crop step, variants, P1–P6, admin queue filter, events, restore rule | DB13 | with B48 |
| B48 | BE | Identity: the `avatar_ref` handler, `UserSummary.avatar_ref`, `Me.avatar`, `PasswordChanged` emit; `avatar` on every module's `UserRef` (null for unavailable accounts and on public endpoints) | DB14 | with B47 |
| B49 | BE | `notifications` skeleton: registry, `_limits`, models, `_write.notify`, catalogue and renderers, N1–N7, preferences, mutes, caps, purge job | DB12, B46 | |
| B50 | BE | Handlers for deeds, feed, media, scoring and identity events (§4.1); grouping; `feed.service` reads (`visible_deed_ids`, `follower_ids_page`, `earlier_commenter_ids`); `fanout_network` | B49, B47, B48 | |
| B51 | BE | Fraud: emit `MemberReviewUpdate` at the five points, `fraud.remind_explanations`, the `review_update` handler | DB15, B49 | with B50 |
| F26 | FE | `Avatar` with refs and defaults, `useAvatarUrls` batching, every call site in §6.2 including the header and admin screens | B47/B48 contract | with F27 |
| F27 | FE | Avatar editor (crop, preview, mobile and desktop), profile buttons, report sheet, admin queue filter | B47 contract | |
| F28 | FE | Bell, notifications page, row menu (read/unread, mutes), settings page | B49 contract | with F26 |
| QA12 | QA | Backend tests per §8 | B47–B51 | |
| QA13 | QA | Frontend tests per §8 | F26–F28 | |
| SEC6 | SEC | Review per §9 | QA12, QA13 | |
| AD5 | AD | Diagrams: the notifications module and fan-out job; the avatar pointer flow (media → identity → UserRef → batched URLs) | SEC6 | |
| DOC6 | DOC | CLAUDE.md (notifications module, avatar rules, import allow-lists), ROADMAP (M6A/M6B split, challenges placement), PRD §6.5 (profile pictures, public pages), §6.7 catalogue, §8 User.avatar / Notification | AD5 | |

---

## 8. Test plan (qa-engineer)

### Notifications

- Each catalogue kind is created once per event (the handler run twice → one row), for the right people, never for the actor. An inactive recipient gets nothing.
- Grouping: 5 likes from 4 people (one unlike, then like again) → one row with count 4 and the newest 3 actors. Read it, then a new like → a new row. Comments and replies group per deed, follows per day.
- Replies (D111): A comments, then B comments → A gets a `reply` row and the doer and reporter get `comment` rows. A comment older than 30 days → no reply row. Deleted comments are ignored. A deed mute stops replies.
- Network (D110): a verified deed by someone I follow → one `network` row today; a second deed the same day joins it. A hidden deed → not shown. Muted actor → nothing. The per-actor daily cap (4th deed) → no fan-out. The recipient cap → status `capped`. A restart resumes from the cursor. The doer and reporter get no `network` row for their own deed. A config change of `network.audience` changes the recipients.
- Caps: the 31st activity row of the day is not created, but existing groups still grow. *About you* and *Reviews* have no cap.
- Read-time rendering: a name change shows the new name. A withdrawn or hidden deed → "This deed is no longer available", no link. A suspended actor → "Unavailable account" and the silhouette.
- Pending invite deeds (`invites_on`) → no row names the doer.
- Preferences: locked categories refuse off (409). Defaults come from config. Mutes are silent and per person or deed; the mute cap holds.
- Read/unread, read-all, the seen count (N2/N3), keyset paging, and purging after 90 days.

### Reviews (D101)

- `MemberReviewUpdate` is emitted for exactly the five kinds, and the reminder fires once, only when there is no reply, N days before the due date. Nothing is emitted when a case opens, is parked or held, is dismissed without an explanation request, or on `PointsChanged(notify=false)`.
- A text scan of every notification text finds no "fraud", "flag", "case", "ring", "detector", case type, other subject's name or admin name.
- Architecture: notifications imports from fraud only `MemberReviewUpdate` and has no handler for the listed fraud events.

### Profile pictures

- Upload → crop → ready → `AvatarChanged` → `users.avatar_ref` → `UserRef.avatar` in feed, deed, comments, Stories, leaderboards, Featured City, connections, scan match, profile, Reviews, notifications and admin responses. The public deed page and the signed-out profile → null.
- Validation reuses M3: a wrong type, a size over the limit, an animated image, a pixel bomb or a type mismatch is rejected. EXIF and GPS are gone from every variant. Corners outside the crop are not in any variant. An image too small → 422.
- Replace: the old picture's objects are gone once the new one is ready, and an old signed URL → 404. If processing fails, the old picture stays.
- Owner removal and admin removal: links stop at once. Admin removal moves the picture to `held/`, restorable for 90 days. Restore with a newer picture → 409. The owner gets `avatar_removed`. The cooldown and the daily change limit hold.
- `avatar_urls`: refs of removed pictures or inactive owners → null; more than 100 → 422; signed out → 401; the rate limit holds.
- Out-of-order `AvatarChanged` events never overwrite a newer ref.
- Reports: avatar reports enter the queue with children first. The owner can't decide their own. Visitors can't report avatars.

### Frontend

- The avatar shows the default first, then the image. One batched URL call per screen. On error: one refresh, then the default. The silhouette shows for unavailable accounts.
- The editor works with touch, mouse and keyboard. The preview matches the crop. Every error state shows.
- The bell count, list tabs, tap-through and mark read, the row menu (read/unread, mutes), and the settings switches with their locked states.

---

## 9. Security and privacy concerns (security-reviewer)

- **D101 through a new surface:** check every notification text and the text scan test. `MemberReviewUpdate` carries no case data, and notifications can't import other fraud events.
- **Avatars:** a face is personal data. Pictures go only to signed-in viewers (D119). `avatar_urls` needs login, caps refs, and refuses refs of inactive accounts or removed pictures. Refs are random and unguessable. Metadata is stripped and the crop is done on the server. Removal moves or deletes objects, so the 24 h URLs die at once. Impersonation and child reports go to the front of the queue. Suspended accounts' pictures stop being signed (and M7 must move their objects).
- **Scraping:** a signed-in account could still collect avatars from long lists; rate limits on `avatar_urls` and on the feed apply.
- **Admin screens with pictures (D124):** a face could bias a fraud review. Reviewers decide on deeds and patterns, and appeals go to a different admin (M5 D100). Flag it if you think fraud screens should show names only.
- **Fan-out cost and abuse:** the per-actor daily cap, the recipient cap, batching and one row per recipient per day stop a busy or popular account from loading the worker or flooding bells. Like, follow and reply grouping with dedupe stops someone from spamming another person's bell by liking, unliking or following repeatedly.
- **IDOR:** notification ids, mutes and preferences are own data only (404).
- **Read-time rendering:** no stored text, so no stale names or deed text survive a hide, withdrawal or deletion.

---

## 10. Before-launch list (additions for ROADMAP)

- PO approval of the notification copy and the profile-picture section of the photo guidelines.
- Privacy notice: profile pictures (who sees them, retention after removal), notification history (90 days).
- Legal check: children in profile pictures (Q94).
- M7: blocking must hide and move avatars, and a blocked person must not be able to notify the person who blocked them.
- M6B items (§12).

---

## 11. Questions for the PO (M6A)

There are 11 questions in three batches. Batch 1 blocks the most design; please answer it first. The email-provider and push questions are deferred to M6B (§12).

### PO answers (decision log)

| Q | Answer (2026-11-27) |
|---|---|
| Q86 | A. Profile pictures are seen by signed-in members only; public pages show the default avatar |
| Q87 | A. Shown at once; anyone signed in can report it to the existing photo queue (children first); only a moderator or admin removes it |
| Q88 | **D.** M6A: initials on a soft-coloured circle, styled the same everywhere; a neutral silhouette for unavailable or deleted accounts. No illustrated picker now. Later (M6B or after): about 12 friendly kindness-themed illustrated avatars that members can choose. Keep M6A simple |
| Q89 | A. Followers of the reporter or the doer, one grouped row a day, with caps (D110) |
| Q90 | A. "Replies" = "also commented": earlier commenters (last 30 days) get one grouped row per deed and can mute that deed; no threads in M6A |
| Q91 | A. All review updates notify (explanation request, one reminder 3 days before the reply is due, decision, appeal outcome, "we've finished looking"), neutral words only (D101) |
| Q92 | A. Like rows are grouped and name the likers ("Ravi, Sneha and 4 others liked the deed about you"), to the doer and reporter only |
| Q93 | A. No new notification on withdrawal: the earlier recognition row changes to "This deed is no longer available", never says who withdrew it, and creates no negative interaction between members; honest corrections stay blame-free |
| Q94 | A. No children in profile pictures (guidelines); a "Shows a child" report goes to the top of the queue; revisit after legal advice |
| Q95 | A. After an admin removal the owner is told why (link to the guidelines), the picture is held privately for 90 days so it can be restored, and a new upload is possible after 24 hours |
| Q96 | A. Monthly challenges become **M6C**, right after M6A and before M6B, on local infrastructure only (§13 is its starting design) |
| Defaults | All architect defaults at the end of §11 approved as listed |
| Q97 | A. (2026-11-28, from SEC6-L7) Fraud case queue, case review and appeal screens show initials only, never profile pictures, to avoid appearance bias; every other screen (including other admin screens) shows pictures as approved. Replaces D124 for those screens |

### Batch 1: blocks the design

**Q86. Who can see profile pictures?**
Example: Kavya adds a photo of herself. A stranger opens a deed link someone shared on WhatsApp.
- **A. Signed-in members only.** Public pages (the shared deed link, the signed-out profile view) show the default avatar.
  - Pros: matches the PRD rule "public pages show no personal information"; faces can't be collected from public links; the app still feels personal inside.
  - Cons: shared deed pages look plainer.
- **B. Everyone, including public pages.**
  - Pros: shared links look more personal and may attract sign-ups.
  - Cons: goes against the PRD privacy rule; faces end up in other services' caches, which we can't take back.
- **C. Only people who follow the owner, or whom the owner follows.**
  - Pros: the most private.
  - Cons: leaderboards and comments would mix photos and initials, which feels unfair and confusing; more complex.

**Recommendation: A.** It keeps the PRD's privacy promise and long-term trust, and only costs some decoration on public pages.

**Q87. How are profile pictures moderated?**
Example: Rahul uploads a picture of a film star as his profile photo.
- **A. Shown at once. Anyone signed in can report it, reports go to the existing photo queue (children first), and only a moderator or admin removes it.**
  - Pros: the same rules people already know from deed photos; no waiting; simple.
  - Cons: a bad picture is visible until it is reported and reviewed.
- **B. Every new picture waits for approval before others see it.**
  - Pros: nothing bad is ever shown.
  - Cons: a review for every upload; delays that discourage people; reviewers become a bottleneck.
- **C. Shown at once, plus an automatic check that flags pictures (never removes them) for review.**
  - Pros: catches problems earlier.
  - Cons: needs an AI service (not local, so not M6A) and a legal check; the AI pre-screen belongs to M7.

**Recommendation: A.** It trusts people by default and reuses a reviewed process (M3 D42, Q44). The M7 pre-screen can be added later.

**Q88. What should the default avatar look like?**
Example: Ahmed hasn't added a photo yet.
- **A. His initials on a soft colour, the same colour everywhere (built today).** A neutral silhouette is used only for "Unavailable account".
  - Pros: already built and familiar; easy to tell people apart in lists; no images to store.
  - Cons: initials can look plain next to photos.
- **B. The same neutral silhouette for everyone.**
  - Pros: calm and uniform.
  - Cons: lists of identical grey figures are hard to scan; loses something people already have.
- **C. A set of friendly illustrated avatars (for example 12 kindness-themed icons). People can pick one; a generated one is the default.**
  - Pros: playful and warm; gives people without a photo a choice.
  - Cons: design work for the icon set and its approval; a "choose" step to build.
- **D. A, plus C's picker later as a small add-on.**
  - Pros: ships now, adds warmth later.
  - Cons: two changes instead of one.

**Recommendation: A**, with D as the path if you want more warmth later. It is consistent, already familiar, and needs nothing new.

**Q89. Who hears about the good deeds of people they're connected to?**
Example: Sneha follows Priya, and Ravi follows Sneha. Priya recognises Ahmed for helping an elderly man.
- **A. Everyone who follows the reporter or the doer** (the same people who see the deed in their Following tab). One grouped row a day: "Priya and 2 others you follow shared good deeds today."
  - Pros: matches what following means in the app; encourages people by showing kindness around them; one row a day keeps it calm.
  - Cons: people who follow many active members get a daily row most days (they can switch it off).
- **B. Only "friends", meaning people who follow each other.**
  - Pros: closest to "friends"; fewer rows.
  - Cons: a new idea the app doesn't have anywhere else; many one-way followers would never hear anything.
- **C. Followers, plus the people the reporter or doer follows.**
  - Pros: widest reach.
  - Cons: tells people about deeds of someone who follows them, whom they never chose to hear about; noisy.
- **D. Only people who tap "Get notified" on someone's profile** (Instagram's bell).
  - Pros: fully chosen by the person; the quietest.
  - Cons: most people never find the bell, so the feature does little.

**Recommendation: A.** It follows the person's own choice to follow someone, celebrates good deeds in their circle, and the daily grouping and caps (D110) keep it from becoming noise. The audience is a config value, so changing to B later is a setting.

### Batch 2: notification behaviour

**Q90. What should "replies" mean, now that comments have no threads?**
Example: Kavya comments "So kind!" on the deed about Ahmed; later Ravi comments too.
- **A. "Also commented": earlier commenters (last 30 days) get one grouped row per deed ("Ravi and 2 others also commented on a deed you commented on"), and can turn it off for that deed.**
  - Pros: works with today's comments; the people in a conversation know it continues.
  - Cons: on a popular deed, commenters get a row each time it becomes unread again.
- **B. Only when the doer or the reporter comments after you ("Ahmed replied on his deed").**
  - Pros: very quiet; feels like a real reply.
  - Cons: misses conversations between other people.
- **C. Build real threaded replies (a Reply button under a comment) in M6A.**
  - Pros: exact replies, like Facebook.
  - Cons: changes comments, moderation and the comment UI; delays M6A.

**Recommendation: A.** It gives conversation feedback without changing comments. Grouping, the 30-day limit and the per-deed mute keep it quiet. Threads can come later.

**Q91. Which review updates (M5) should notify?**
Example: An admin asks Priya to explain some recent deeds; later she gets a decision and appeals it.
- **A. All of them: the explanation request, one reminder 3 days before the reply is due, the decision notice, the appeal outcome and "we've finished looking". Neutral words only.**
  - Pros: an honest person always knows when to explain and what happened (principle 3).
  - Cons: a colluder learns sooner (already accepted in M5).
- **B. Decisions and appeal outcomes only; requests stay as the Profile dot.**
  - Pros: fewer messages about reviews.
  - Cons: people miss the chance to explain, which is the step that protects them.
- **C. All of them, without the reminder.**
  - Pros: no nagging.
  - Cons: one missed row and the 14-day window passes.

**Recommendation: A.** The explanation step only protects genuine contributors if they know about it, and the reminder is our cheapest fairness safeguard.

**Q92. Should like notifications say who liked?**
Example: Ravi, Sneha and four others like the deed about Ahmed.
- **A. Grouped with names: "Ravi, Sneha and 4 others liked the deed about you."**
  - Pros: warm and encouraging; one row instead of six.
  - Cons: today nobody can see who liked a deed; this tells the doer and the reporter (only them).
- **B. Grouped, count only: "6 people liked the deed about you."**
  - Pros: likers stay anonymous, as today.
  - Cons: less personal.
- **C. No like notifications.**
  - Pros: the quietest.
  - Cons: misses an easy, harmless encouragement.

**Recommendation: A.** Knowing who appreciated your deed is encouraging, and only the two people already named on the deed are told. Switching to B is a one-line change.

**Q93. Should the doer be told when a recognition about them is withdrawn?**
Example: Priya recognises Ahmed, then withdraws it two hours later because she picked the wrong person.
- **A. No new notification. The earlier "You were recognised" row changes to "This deed is no longer available".**
  - Pros: kind and quiet; no blame between people; matches M3's quiet removals.
  - Cons: Ahmed may wonder where it went.
- **B. "Priya withdrew a deed about you."**
  - Pros: fully transparent.
  - Cons: can cause friction; may put reporters off fixing mistakes.
- **C. "A recognition about you was withdrawn" (no name).**
  - Pros: transparent without blame.
  - Cons: still a negative message about an honest correction.

**Recommendation: A.** It protects both people and keeps reporters willing to correct mistakes.

### Batch 3: profile picture details and challenges

**Q94. Can a profile picture show a child?**
Example: Sunita uses a photo of herself holding her baby.
- **A. No children. It's in the guidelines, and a "Shows a child" report goes to the top of the queue.**
  - Pros: protects children's data (DPDP); a profile picture appears on every comment and board, far more often than a deed photo; a clear rule.
  - Cons: some parents will be disappointed.
- **B. Allowed if the account holder is also in the photo.**
  - Pros: friendlier.
  - Cons: hard to judge; children's faces repeated across the app; legal advice on children's photos is still open (ROADMAP M1).
- **C. Allowed.**
  - Pros: no restriction.
  - Cons: the widest exposure of children's images in the app.

**Recommendation: A**, until legal advice says otherwise.

**Q95. What happens when an admin removes someone's profile picture?**
Example: An admin removes Rahul's film-star picture after a report.
- **A. Rahul is told why (with a link to the guidelines), the picture is held privately for 90 days so it can be restored, and he can upload a new one after 24 hours.**
  - Pros: transparent; a mistake can be undone (as for deed photos, Q32); the short wait stops instant re-uploads.
  - Cons: a 24-hour wait even for a sincere fix.
- **B. The same, but he can upload again at once.**
  - Pros: no waiting.
  - Cons: the same picture can come straight back.
- **C. The same as A, and 3 removals in 90 days block uploads for 30 days.**
  - Pros: stops repeat abusers.
  - Cons: more rules; M7 blocking will cover repeat abuse.

**Recommendation: A.** Clear, fair and reversible, and it reuses the M3 removal rules.

**Q96. Where should monthly kindness challenges go?** (PRD §6.8; design parked in §13)
- **A. A separate milestone M6C, right after M6A and before M6B.** It uses only local infrastructure, like M6A.
  - Pros: keeps M6A small; challenges can announce themselves through the new in-app notifications; no cloud needed.
  - Cons: pushes M6B (email, push) later.
- **B. Inside M6B, with the cloud work.**
  - Pros: fewer milestones; sponsored-challenge emails can be built in one go.
  - Cons: mixes a product feature with infrastructure, so one blocks the other.
- **C. With M8 (rewards, sponsors and ads).**
  - Pros: sponsored challenges, sponsor data and any prizes are designed together, with eligibility and tax rules.
  - Cons: no challenges for months; PRD §6.8 lists them as an MVP feature.

**Recommendation: A.** Challenges encourage good deeds and need nothing from the cloud. Keeping them separate keeps each review small, and sponsorship can stay minimal until M8.

### Defaults decided by the architect (needs PO OK, not asked as questions)

- D107: five categories; *Account & safety* and *Reviews* can't be switched off.
- D108: groups for likes, comments and replies per deed, and for follows and network per day; 3 names shown.
- D110: per actor, 3 fan-outs a day; at most 5,000 recipients per deed; the network row links to Home (Stories).
- D113: muting a person doesn't stop recognitions of you; mutes are silent.
- D115: 30 *Activity* rows a day per person; history kept 90 days.
- D112 / §2.4: the reminder 3 days before an explanation is due.
- D116 / D118: avatar sizes 96/256/512 px; URLs valid 24 h with a 12 h step; at most 5 picture changes a day; minimum 256 × 256 px.
- D121: avatar reports from signed-in members only; the new reason "Not this person / impersonation".
- D124: pictures on admin screens too (the PO asked for consistency; see the §9 note on fraud reviews).
- A21: "Your deed was verified" only for invite deeds.
- A26: an in-app "Your password was changed" row now; the email comes in M6B.
- Header: my small avatar next to "Profile".

---

## 12. M6B Cloud Integration (after M6A; outline only)

M6B adds delivery channels and production services. It is designed in full after M6A is approved. Scope as known now:

- **Email delivery.** `core/email.py` behind a provider interface (`console` for development, `smtp` so the provider can change by settings). A `notification_deliveries` table and a worker job `notifications.send_email` that claims rows with `SKIP LOCKED`, retries and records results. Templates with escaped values only, one-click unsubscribe per category (signed tokens, `List-Unsubscribe` headers), suppression after hard failures, and SPF/DKIM/DMARC on the sending domain. Default email kinds (proposal): password changed (always), review updates, recognitions of you, Kindness Hero titles, moderation removals.
- **Email verification** for password accounts (ROADMAP M1 Deferred): a single-use hashed link valid 24 h; emails only to verified addresses.
- **Password-changed email** on every reset (PRD §6.1, ROADMAP M1 Deferred), from the M6A `PasswordChanged` event.
- **Browser push (FCM).** `core/push.py`, `push_devices`, a `send_push` job, a service worker and a web app manifest (iOS needs Home Screen install), permission asked only after the person chooses, quiet hours 22:00–08:00 IST, a daily cap, and minimal lock-screen text (never review details).
- **Production object storage:** the GCS `asia-south1` bucket with HMAC keys through the existing S3-compatible `core/storage.py` (a settings change, M3 D35), plus CORS for the web origin, held for avatars as for deed photos.
- **Settings and secrets:** `GD_EMAIL_*`, `GD_SMTP_*`, `GD_PUSH_*`, `GD_FCM_*`, refused in production when unsafe, like SMS.

**Deferred questions (to be numbered when M6B is designed):** M6B-1 which email service (Revision 1 recommended Amazon SES Mumbai through SMTP); M6B-2 whether to build push and how (Revision 1 recommended building it behind a config switch); M6B-3 which kinds go by email and push by default.

---

## 13. Parked: monthly kindness challenges (Revision 1 design, kept for later)

Not in M6A or M6B; placement is Q96. This is the Revision 1 design, kept so nothing is lost. It is to be revised when scheduled.

- **Module `challenges`.** Imports `app.core`, `deeds.service`, `identity.service` and only `MonthFinalized` from `scoring.service`; never `feed`, `media` or `fraud` (joins `SCORING_NAMES` in the architecture test).
- **Model.** `challenges` (`period_month`, title, description, `rule_type` `doer_deeds`/`reporter_deeds`, `target` 1–30, `category_slugs`, `min_distinct_reporters`, status `draft`/`published`/`cancelled`, `sponsor_id`; rules frozen at publish by a trigger; at most 3 a month). Participation lives in `challenge_participants` (joined, left, progress, distinct reporters, completed, final) and `challenge_progress_deeds`. `sponsors` holds only a name, an optional https website and a status.
- **Rules.** Admin-only authoring, audited. Counted, verified deeds in the IST month; same-act extras don't count; a reporter minimum applies; withdrawal and rejection remove progress; progress is final at `MonthFinalized`. Likes and comments never count. Challenges never change points, leaderboards, titles or badges. Completion gives a "Challenge completed" mark on the profile. No sponsor logo or prizes until M8, and no member data goes to sponsors.
- **Events and notifications.** `ChallengePublished` (fan-out in batches), `ChallengeEndingSoon`, `ChallengeCompleted`, `ChallengeCancelled`.
- **Screens.** A Challenges page and a detail page, a Home card, the profile section, and admin pages for challenges and sponsors.
- **Questions to ask when scheduled:** does completing a challenge affect points or the leaderboard (Revision 1 recommended no); do people join with a tap (recommended yes); do deeds from before joining count (recommended yes, the whole month); can sponsored challenges offer prizes before M8 (recommended no); what does completing give (recommended a profile mark).
