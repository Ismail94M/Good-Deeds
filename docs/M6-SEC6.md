# M6A Profile Pictures & Notifications: security review SEC6 (2026-11-27)

**Scope:** `git diff a79d7b3..HEAD` on `m6-notifications` (f457133 design rev 2, e23e23a migrations 0030–0033, 6fe9ded, 2d613f4, 7cb3f2d, 282d23d, 4a900f5 at HEAD): new `notifications` module, `media` profile pictures (`_avatars.py`, `_pipeline.py` crop, `_worker.py`, `_admin.py` avatar decide/restore, `avatar_urls`), `identity` avatar pointer and `PasswordChanged`, `fraud` `MemberReviewUpdate`/`ReviewsSeen`/reminders, `feed.service` reads, every `UserRef` gaining `avatar`, and the frontend (`Avatar`, `useAvatarUrls`, editor, bell, notifications pages). Read-only review against `docs/M6-DESIGN.md` (§0, §3, §4, §9, §11 PO answers) and CLAUDE.md. Checks run: `tests/test_architecture.py` (22 passed), `tests/notifications/test_qa_m6.py` and `tests/media/test_qa_m6_avatars.py` on `gooddeeds_test_db` (23 passed).

**Result:** no Critical or High findings. One Medium, seven Low. The picture pipeline, URL signing and notification IDOR rules are sound. The findings are about cost (the "also commented" fan-out has no cap), rows that outlive their deed, and small gaps against PO rules (Q86 on the invite preview, Q95 cooldown).

| ID | Severity | Summary |
|---|---|---|
| SEC6-M1 | Medium | "Also commented" (reply) fan-out has no recipient cap and runs inside the `CommentPosted` handler transaction |
| SEC6-L1 | Low | Network rows store no deed id, so they keep naming the actor after the deed is withdrawn, hidden or rejected |
| SEC6-L2 | Low | Signed-out invite preview returns the reporter's avatar ref (Q86/D119: public responses carry `avatar: null`) |
| SEC6-L3 | Low | The 24 h re-upload wait after an admin removal (Q95) is checked only in `PUT /me/avatar`: an upload queued before the removal goes live at once |
| SEC6-L4 | Low | `DELETE /notifications/mutes` (person) answers 204 for a suspended handle and 404 for an unknown one (account-state oracle) |
| SEC6-L5 | Low | Retention: `notification_fanouts` (deed id + actor ids) kept forever; dedupe purged at 30 days while grouped rows live 90 |
| SEC6-L6 | Low | `avatars_enabled = false` stops new pictures but keeps signing existing ones; no fast "hide all pictures" switch |
| SEC6-L7 | Low | Pictures on fraud case and appeal screens (D124): bias risk the design asked to be flagged |

---

## Findings

### SEC6-M1 (Medium): the "also commented" fan-out has no cap

- **Location:** `backend/app/modules/notifications/handlers.py:146-166` (`on_comment_posted`: one `notify` per earlier commenter); `backend/app/modules/feed/service.py:184-211` (`earlier_commenter_ids`: every distinct author in the last `replies.lookback_days`, no limit).
- **Description:** D110 caps the network fan-out (per actor per day, 5,000 recipients per deed, batched in a job). The reply fan-out has none of this. For each new comment, the handler loads every distinct earlier commenter of the last 30 days and calls `notify` for each, in the one handler transaction. `notify` runs about 4–6 queries per recipient (`is_active`, preference, mute, dedupe claim, fold or insert, daily slot), even when the result is `DUPLICATE`.
- **Exploit:** A deed reaches the Top feed and collects comments from 3,000 people within 30 days. Each new comment costs about 15,000 queries in one transaction. A spammer (or a few accounts) posting up to `rate_comment_user_daily` comments a day on that deed keeps the `notifications` handler queue busy. Long transactions also hold row locks on many recipients' unread group rows, so they contend with other handlers. The bells are protected (dedupe per actor per group, the Activity cap of 30), but the worker is not. The same deed can be targeted again every day.
- **Fix:** Cap recipients per comment (for example `replies.max_recipients_per_comment`, 200, newest commenters first: `ORDER BY max(created_at) DESC LIMIT n` in `earlier_commenter_ids`). Alternatively, move the reply fan-out into the same deferred, batched job as the network fan-out. Consider also skipping the fan-out when the author already has a reply claim on this deed today.

### SEC6-L1 (Low): network rows outlive their deed

- **Location:** `backend/app/modules/notifications/_fanout.py:20-21, 157-168` (no `deed_id` on the row, by decision (b)); `_render.py:207` (the visibility re-check needs `row.deed_id`), `:222-227`.
- **Description:** D109 says network rows are shown only while the deed is visible, and Q93 says a withdrawn deed must not leak through old rows. Because network rows have no deed id, the render step can't re-check them. "Priya, who you follow, shared a good deed today" stays for 90 days after Priya withdraws the deed, after the doer hides it, or after it is rejected once a fraud decision is final. Only the actor's name stays; the deed itself isn't shown. So this is minor, but it differs from D109.
- **Exploit:** Ahmed is wrongly recognised and the reporter withdraws the deed within minutes. Ahmed's followers already got "Ahmed, who you follow, shared a good deed today", and that row keeps saying so. A doer who hides a deed has their followers told the same.
- **Fix:** Store the contributing deed ids per actor in `data` (for example `{"deeds": {actor_id: [deed_id, …]}}`, at most `actors_kept` actors). On read, drop actors none of whose deeds are still visible to the viewer, and show "This deed is no longer available" (or hide the row) when none are left. Alternatively, accept the gap and record it in the design as a PO decision.

### SEC6-L2 (Low): the signed-out invite preview carries an avatar ref

- **Location:** `backend/app/modules/deeds/_invites.py:289-296` (`preview`, no login: `avatar=reporter.avatar_ref`).
- **Description:** D119/§3.1: "Public endpoints … force null". `GET /invites/by-token/{token}` is public and now returns the reporter's ref. A visitor can't turn it into a URL (P5 needs `CurrentUser`), and the frontend doesn't ask while signed out. Still, the ref holds the photo id, and the rule is meant to hold for every public response. Invites are switched off (D17), so nobody can do this today.
- **Fix:** Set `avatar=None` in `preview`. Add the invite preview to the "public responses never carry an avatar" test next to `/api/public/deeds/{id}` and the signed-out `/users/{handle}`.

### SEC6-L3 (Low): an upload queued before an admin removal skips the 24 h wait

- **Location:** `backend/app/modules/media/_avatars.py:225-231` (`cooldown_until` checked only in `set_avatar`); `_avatars.py:259-291` (`make_room` finds no live picture and lets the new one in); `_admin.py:605-610` (removal doesn't touch uploads still in work).
- **Description:** Q95 says a new upload is possible 24 hours after an admin removal. The wait is checked when an upload is queued, not when it goes live. Admin removal doesn't cancel the owner's uploads that are `queued` or `processing`.
- **Exploit:** Rahul sees his film-star picture has been reported. He queues a second copy (or a similar picture) with `PUT /me/avatar`. When the admin removes the first one, the queued one goes live at once with no wait. The window is short (worker latency), so this needs timing or a stuck queue. Separately, nothing stops the *same bytes* coming back after 24 h. Deed photos have a hash check for doer removals (Q29) but avatars don't.
- **Fix:** In the avatar removal branch of `decide`, mark the owner's in-work avatar rows `deleted` (as `remove_avatar` does), or check `cooldown_until` again in `make_room`/`_finish_ready` and supersede the new row if it falls in the wait. Optionally, refuse a new avatar whose `content_sha256` matches an admin-removed one of the same owner within the retention period.

### SEC6-L4 (Low): unmuting a person reveals whether a handle exists but is inactive

- **Location:** `backend/app/modules/notifications/_prefs.py:160-167, 222` (`unmute` calls `_target(..., must_be_visible=False)`).
- **Description:** `GET /users/{handle}` answers 404 for an inactive account and for an unknown one alike. `DELETE /notifications/mutes {type: person}` answers 204 for any existing handle, active or not, and 404 only for an unknown one. A member can tell "suspended or blocked" apart from "never existed". The rate limit is 60 per minute.
- **Fix:** Find the mute row by joining on the handle. Answer 204 when a row was deleted, and otherwise do what `mute` does (404 unless the account is active). Unmuting someone who is muted keeps working because that row exists.

### SEC6-L5 (Low): retention gaps

- **Location:** `backend/app/modules/notifications/_purge.py:1-5` (finished `notification_fanouts` kept "until account deletion is built"); `_purge.py:43-47` with the `dedupe_days` 30 / `retention_days` 90 config.
- **Description:**
  - `notification_fanouts` keeps each verified deed id with its reporter and doer ids forever. That is a second, unbounded record of who was connected to whom, which D115's data-minimisation aim doesn't need after the fan-out finishes.
  - Dedupe keys are purged at 30 days, but an unread like/comment/reply group can live 90 days. After day 30 the same person can unlike and like again and count twice in the same row. This is a nuisance only, because the count is shown to the doer and reporter.
- **Fix:** Purge `notification_fanouts` rows that are `done`/`capped` and older than a few days. Keep dedupe rows of a group while its unread row exists (purge by `created_at < now - dedupe_days` *and* no unread row with that group key), or set `dedupe_days >= retention_days`.

### SEC6-L6 (Low): no quick way to stop serving every profile picture

- **Location:** `backend/app/modules/media/router.py:419-433` (`avatar_urls` doesn't check `avatars_enabled`); `_avatars.py:86-88` (the switch guards uploads and `PUT /me/avatar` only).
- **Description:** Children's pictures (Q94) still need legal advice. If that advice, or an incident, means pictures must come down at once, turning off `avatars_enabled` only stops new pictures. Existing ones keep being signed for 24 h URLs.
- **Fix:** In `avatar_urls`, return null for every ref while `avatars_enabled` is false (or add a separate `avatars_visible` key), and say in the runbook that URLs already issued live up to `avatar_url_ttl_minutes`.

### SEC6-L7 (Low): faces on fraud review screens (D124, §9 note)

- **Location:** `frontend/src/features/admin/FraudCaseReviewPage.tsx`, `AppealReviewPage.tsx`, `FraudCasesPage.tsx`, `AppealsPage.tsx`; backend `fraud/_review.py`, `_appeals.py` (`_ref` now passes `avatar`).
- **Description:** §9 asked for this to be flagged. A face can bias a confirm, exclude or appeal decision (appearance, perceived age, gender, caste or religion markers), and that decision can cost someone a cash prize. On photo-report screens a picture helps (impersonation). On fraud screens it adds no evidence, because the decision rests on deeds and patterns.
- **Fix (PO decision):** show initials only on fraud case, appeal and override screens (pass `avatar: null` from `fraud` admin reads, or `unavailable`-style rendering in those pages). Keep pictures on the photo-report and comment-report screens.

---

## Verified OK

**Profile pictures**
- **Upload:** `purpose = avatar` goes through the M3 presigned PUT, `complete` (HEAD and magic bytes), and the declared type and size checks. Uploads need `OnboardedUser`, and the upload limits apply.
- **Worker:**
  - The pipeline is decode → validate → orient → colour → crop → derive → encode. `max_image_pixels` and `DecompressionBombError` still apply.
  - Animated images are rejected.
  - `crop_square` clears `info` and the WebP is re-encoded with no metadata, which the QA test checks.
  - The raw upload is deleted after processing.
  - `upright_size` reads the header only and fails closed (returns None, so the worker decides).
- **Crop input:** `AvatarCropIn` bounds x, y and size to 0..1 with `allow_inf_nan=False`, and a DB check constraint backs this. `crop_box` refuses squares outside the image (beyond a 2 px or 0.2 % slack) and squares below `avatar_min_side_px`. A header mismatch in the worker leads to `CORRUPT`.
- **Storage keys:** chosen by the server from a random `uuid4` photo id. A ref is `{photo_id}-{version}`, parsed by a strict regex.
- **`POST /media/avatars/urls`:**
  - It needs `CurrentUser` and has a per-account rate limit.
  - It caps unique refs by config and the body at 1,000 strings of up to 80 characters.
  - It signs only `purpose = avatar`, `ready`, `takedown = none` rows with no pending move, whose objects aren't purged, whose version matches and whose owner is active.
  - Callers can't sign deed photos, self-log photos, held, removed, replaced or pre-restore objects, or other keys.
  - Every ref comes back, with null for anything else, so the answer reveals nothing beyond "live or not".
- **Q86:**
  - `/api/public/deeds/{id}` forces `avatar: null` for doer and reporter.
  - `GET /users/{handle}` gives the ref only to a signed-in viewer, through `OptionalUser`.
  - The deed-counts, social and public scoring endpoints carry no refs.
  - There is no server-side OG data: `index.html` meta is static.
  - The frontend `useAvatarUrl(s)` asks only when `AuthContext.user` is set, and sign-in or sign-out drops the cached URL queries (`clearUserData`).
  - The invite preview is the exception (L2).
- **Inactive accounts:** `public_summaries`, `public_profile`, `ResolvedDoer` and notification `user_refs` give null refs or the silhouette. Scoring and fraud placeholders default to `avatar: null`.
- **Admin decisions:**
  - `ReviewerUser` is required.
  - Uploaders and reporters of the case are excluded from the queue and the detail (`_uninvolved`, `_involved`), and from restore (`case_ids=None`).
  - Removal sets `takedown = admin_removed` and `HOLD`, emits `AvatarChanged(None)` and `AvatarModerationDecided`, and writes an audit row with the owner id.
  - Restore needs `READY`, no purge and a removal from a case. It answers 409 `avatar_replaced` when a newer picture is live, and makes a new version, so pre-removal URLs and refs never work again.
  - `impersonation` is refused for deed photos.
- **Races:** all serialised by the `media_avatar` advisory lock. The lock order is `photo_case` → `media_avatar` → rows, and no path takes them the other way round.
  - Replace vs replace: `attached_at` supersede.
  - Replace vs owner removal: in-work rows are deleted, and the worker's `_locked_if_ours` then drops its output.
  - Replace vs admin decide: replacing closes the open case first.
  - Restore vs go-live: `run_move` and `make_room` both lock the photo row, so a purge always sees the final `variant_keys`.
  - The identity handler ignores older `changed_at`.
- **Purge:**
  - Owner removal and replacement delete the objects right after commit, with `purge_deleted` as the backstop.
  - Held avatars are purged by `purge_removed` after `removed_photo_retention_days`, which has no purpose filter.
  - Reports on avatars need a signed-in member and an active owner. Visitors get the same 404 as for an unknown photo.

**Notifications**
- **IDOR:**
  - N1 lists and N2 counts filter by `user_id`.
  - N4 read and unread use `_own` (`id` and `user_id`, 404 otherwise), and read-all is scoped to the caller.
  - N5 and N6 work on the caller's rows only.
  - N7 mutes are keyed by the caller. Muting a deed needs `visible_deed_ids`, so it can't probe deeds.
  - The cursor is tab-bound and validated.
  - All routes need `CurrentUser`, with per-account read and write limits.
- **D101:**
  - Review rows say only "There's an update for you in Reviews" or the explanation request or reminder with a due date.
  - `MemberReviewUpdate` carries the user, kind, the member's own request or notice id, and the due date. `ref_id` and `data` are never returned.
  - `notifications` imports only `MemberReviewUpdate` and `ReviewsSeen` from fraud, and the architecture test proves it can't subscribe to `FlagRaised`, `FlagCaseResolved`, `DeedHoldChanged`, `FlagDecisionFinal`, `HeroTitleRevoked` or `PointsChanged`.
  - `ReviewsSeen` (user id, time) is member-safe. It is a second allowed name beyond the design's "only `MemberReviewUpdate`", so update D104 to match the test.
- **Q93 and D109:**
  - No withdrawal, hide or points notification exists.
  - Recognition, like, comment and reply rows re-check `visible_deed_ids` on read (status `VERIFIED` plus the feed rule). Otherwise they read "This deed is no longer available" with no names and no link.
  - Inactive actors show as "Unavailable account" with no handle or avatar.
  - Pending invite deeds create nothing. Inactive recipients get nothing.
  - The network-row gap is L1.
- **Stored data:**
  - Comment text, deed text and names are never stored. Rows hold ids, codes and numbers only, and `data` holds badge and title labels, region ids and dates.
  - Text is plain and rendered as React text (no `dangerouslySetInnerHTML`). Link targets are built from a fixed `kind` set with `encodeURIComponent`.
- **Fan-out (network):**
  - At most 3 fan-outs per actor per day, counted under an advisory lock.
  - At most 5,000 recipients per deed.
  - Batched in a job with a keyset cursor and `SKIP LOCKED`.
  - Each recipient is checked against `visible_deed_ids`.
  - Only the followed person is named to a recipient, never the other party.
  - 1 network row per recipient per day; the deed's parties are skipped.
- **Spam:** like and unlike, and follow and unfollow, count once per group through `notification_dedupe`. A capped claim is released, so it can't block later counts, and the Activity cap is 30 a day.
- **Outbox payloads:**
  - `AvatarChanged`: user id, ref, time, reason code.
  - `AvatarModerationDecided`: ids and reason code.
  - `PasswordChanged`: user id, `method`, time.
  - `MemberReviewUpdate` and `ReviewsSeen`: as above.
  - No URLs, text, phone numbers or case data.
- **Purge job:** nightly, one transaction, driven by config.
- **D104 boundaries:**
  - `notifications` imports only core, `deeds.service`, `identity.service`, named events and the three feed reads. Nobody imports `notifications`.
  - `identity/handlers.py` reads only `AvatarChanged` from media.
  - `media.service` imports `identity.service` and identity's service imports nothing from media, so there is no cycle.
  - The rules have violation tests. `tests/test_architecture.py`: 22 passed.

**Frontend**
- The `Avatar` component always renders the default first. The silhouette (no initials) is used for unavailable accounts, and the image comes only from server-signed URLs.
- The bell renders and polls only when signed in.
- The poll floor is 15 s, with the server value at 60 s and config bounds of 10–3600.

**Not a security finding:** a title or badge row stays "You're Kindness Hero…" after a revocation, because notifications may not hear `HeroTitleRevoked`. This is consistent with D112. If the PO wants it, the row could be re-checked through a member-safe scoring read.

**Known and accepted:** URLs already issued for a suspended account's picture keep working up to 24 h until M7 moves the objects (D122, §10).

---

## Response (2026-11-28)

All findings fixed on `m6-notifications` (not committed). New migration `0034` (`backend/alembic/versions/2026_11_28_0000-0034_notifications_sec6.py`): `notifications` config v+1 with `replies.max_recipients_per_comment: 200`, and `photos.reject_reason` also allows `cooldown`; upgrade, downgrade and `alembic check` clean.

| ID | Fix | Tests |
|---|---|---|
| SEC6-M1 | Recipients per comment are capped by the new config key `replies.max_recipients_per_comment` (`notifications/_limits.py`). `feed.service.earlier_commenter_ids` takes `exclude` (the doer, reporter and author, so they don't use up the cap) and `limit`, newest commenters first (`GROUP BY author ORDER BY max(created_at) DESC LIMIT n`); `notifications/handlers.py` `on_comment_posted` passes both. Grouping, dedupe and deed mutes are unchanged (a muted commenter still takes a place). The handler now runs at most 200 `notify` calls per comment. | `tests/notifications/test_handlers.py::test_also_commented_reaches_at_most_the_configured_recent_commenters` |
| SEC6-L1 | Network rows still have no `deed_id`. The deed is passed as `Notice.network_deed_id` and kept per actor in `data["deeds"]` (`{actor_id: [deed_id, …]}`), merged on fold or for a repeat actor, and pruned to the actors the row still names (`actors_kept`) (`notifications/_write.py` `_ADD_NETWORK_DEED`, `_fanout.py`). On read, `_render.py` checks those deeds with `visible_deed_ids` and names only actors with at least one deed still visible; the count drops the kept actors who have none. With nobody left the row reads "This deed is no longer available", with no names, link or mute. One hidden deed never blanks the row while another is visible. | `tests/notifications/test_fanout.py::test_a_row_names_only_actors_with_a_deed_still_visible` (replaces `test_a_row_written_stays_after_one_deed_is_hidden`) |
| SEC6-L2 | `deeds/_invites.py` `preview` returns `avatar: None`. | `tests/media/test_qa_m6_avatars.py::test_public_pages_never_carry_a_picture_signed_in_pages_do` now covers `GET /invites/by-token/{token}` |
| SEC6-L3 | The wait is checked again when the picture would go live: `media/_avatars.py` `in_cooldown`, called in `_worker.py` `_finish_ready` before `make_room`. During the wait the upload is rejected with the new reason `cooldown` (staging and variants deleted, the live slot untouched). `GET /me/avatar` reports it as `pending.reject_reason = "cooldown"`, and the editor says "You can add a new photo after {time}" from `removed_notice.upload_after` (`frontend/src/features/media/avatar/AvatarEditor.tsx`, `avatarApi.ts`). **Re-upload of the same image after the 24 h wait is accepted** (no hash check). The wait is meant to slow down instant re-uploads (Q95). A picture that breaks the guidelines again can be reported and removed again, and M7 blocking covers repeat abuse. | `tests/media/test_qa_m6_avatars.py::test_an_upload_queued_before_an_admin_removal_never_goes_live_in_the_wait`; `qaAvatarEditor.test.tsx` "an upload finished during the wait…" |
| SEC6-L4 | `notifications/_prefs.py` `unmute` (person) deletes the mute by the handle's id first and answers 204 if one was removed. Otherwise it answers as `mute` does (404 unless the account is active), so unknown and suspended handles get the same 404. | `tests/notifications/test_prefs.py::test_unmute_answers_the_same_for_unknown_and_suspended_handles` |
| SEC6-L5 | `notifications/_purge.py`: finished fan-outs (`done`/`capped`) go once `finished_at` is older than `retention_days`. Group claims `{group_key}\|{actor}` older than `dedupe_days` are kept while a row of that group still exists for the person, so an unlike and like again can't count twice in a 90-day row. Other keys still go after `dedupe_days`. | `tests/notifications/test_api.py::test_purge_keeps_group_claims_while_the_row_lives_and_drops_old_fanouts` |
| SEC6-L6 | `media.service.avatar_urls` signs nothing while `avatars_enabled` is false: every ref comes back null, and the frontend `Avatar` keeps the default. Runbook note: URLs already issued stay valid for up to `avatar_url_ttl_minutes` (24 h). | `tests/media/test_avatars.py::test_switching_avatars_off_signs_nothing` |
| SEC6-L7 | PO Q97: `fraud/_review.py` and `_appeals.py` `_ref` send `avatar: null`, which covers the case queue, case review and appeal list/detail. The four screens (`FraudCasesPage`, `FraudCaseReviewPage`, `AppealsPage`, `AppealReviewPage`) render `<Avatar avatar={null} …>`. Every other admin screen keeps pictures. | `tests/fraud/test_appeals.py::test_fraud_case_and_appeal_screens_never_carry_profile_pictures`; `qaAvatar.test.tsx` (Q97 sweep and "other admin screens keep pictures"); `FraudCases.test.tsx` "shows initials only…" |

**Not a finding (revoked titles and badges):** scoring now also emits the member-safe `AwardWithdrawn` (user, `award`, title key or badge slug; no case, reason or admin) next to `HeroTitleRevoked`/`BadgeRevoked` (`scoring/events.py`, `_revoke.py`, `service.py`). `notifications/handlers.py` `on_award_withdrawn` marks the award row `data.withdrawn`, so it reads "This is no longer available" with no title and no link. It also records a marker, so an award heard after its withdrawal writes nothing. The architecture allow-list adds exactly `AwardWithdrawn` for notifications, and `scoring.badge_revoked` joins the never-heard list (`tests/test_architecture.py`). Tests: `tests/notifications/test_handlers.py::test_a_withdrawn_title_or_badge_reads_no_longer_available`, `tests/scoring/test_revoke.py`, `tests/notifications/test_qa_m6.py`. D104 in the design should now list `AwardWithdrawn` (and `ReviewsSeen`).

**Also:** a `size_mismatch` upload now reads "That upload didn't finish correctly. Please try again." instead of the connection message (`AvatarEditor.tsx`). Two stale tests were updated: `tests/deeds/test_doer_view.py` expects person refs of exactly `{handle, display_name, avatar}`, and `tests/deeds/test_qa_rev5.py` allows only the avatar cooldown's `extra={"available_at": …}` in `media/_avatars.py`.
