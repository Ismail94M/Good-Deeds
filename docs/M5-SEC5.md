# M5 Fraud & Review: security review SEC5 (2026-11-26)

**Scope:** `git diff 270ee4c..HEAD` on `m5-fraud` (a272654 design rev 4, waves 8f0ae9c, dd07d7e, 90e1404, 6ba7439): `fraud` module, `scoring` overrides/revocation/holds/concentration/barrier, `deeds` fraud reads and rejection, migrations 0026/0027, admin and member frontend. Read-only review against `docs/M5-DESIGN.md` (§0.0, D78–D102, §9) and CLAUDE.md.

**Result:** no Critical or High findings. Six Medium, seven Low. Most Medium findings are gaps between the code and a PO rule (second admin for cuts, irreversible steps wait for appeals, conflicted admins learn nothing), or detector cost and evasion.

| ID | Severity | Summary |
|---|---|---|
| SEC5-M1 | Medium | Audit view has no conflict rule: a flagged or related admin can read case types, co-subject ids and every admin note |
| SEC5-M2 | Medium | Second-admin rule (Q77) can be bypassed with several small cuts in a row |
| SEC5-M3 | Medium | Raising points needs no second admin and sends no notice: one admin can add up to 50 prize-rank points per deed for anyone they aren't related to |
| SEC5-M4 | Medium | A title or badge can be revoked while a co-subject's appeal is pending, and stays revoked after that appeal restores the deeds (D100) |
| SEC5-M5 | Medium | Ring detector: one DB query per candidate cycle with no limit, and overlapping cycles open one case each (cost and queue flooding) |
| SEC5-M6 | Medium | Ring search loads the full 3-hop out-neighbourhood, so it is truncated for well-connected users and can be evaded on purpose; per-deed truncation isn't visible to admins |
| SEC5-L1 | Low | Appeal sent at the window edge can race `finalize_decisions`: notice made final with an open appeal, then deed rejected |
| SEC5-L2 | Low | An overturned override appeal may restore nothing while the member is told "Points restored" |
| SEC5-L3 | Low | `case_id` on an override isn't checked against the person or the admin's visibility (case existence oracle, misleading audit link) |
| SEC5-L4 | Low | Revocation checks the admin's conflict with the title owner only, not with the case; the case needn't relate to the title's month |
| SEC5-L5 | Low | Member reply/appeal share the admin write bucket; `GET /reviews` and `POST /reviews/seen` have no limit |
| SEC5-L6 | Low | `finalize_decisions` idempotency depends on outbox rows never being pruned, and it re-reads all reject outcomes every hour |
| SEC5-L7 | Low | Nightly sweep: one long transaction, in-memory, cost O(edges × search budget) |

---

## Findings

### SEC5-M1 (Medium): conflicted admins can follow their own case through the audit view

- **Location:** `backend/app/modules/fraud/_audit_view.py:58-109` (no visibility filter); data written at `fraud/_explain.py:116-124` (`new_value.user_ids`), `fraud/_actions.py:301-310` (`reason = note`, `new_value.type`), `fraud/_appeals.py:470-487`, `fraud/_notices.py:140-157`, `scoring/_overrides.py:192-213, 298-313`, `deeds/_reject.py:68-77`.
- **Description:** D90 hides a case from a conflicted admin (404 everywhere) so that "a flagged admin can't learn that they are flagged", and so that an admin who is friends with the subjects can't follow or tip off the case. `GET /api/admin/audit` returns every row to every admin with full `reason` (the free-text admin notes) and `old_value`/`new_value`. §3.5 allows conflicted admins to use the audit view, but the M5 rows put case content in it.
- **Exploit:** Admin X has recognised Priya in the last 90 days. A ring case about Priya, Ahmed and Ravi opens; another admin parks it with the note "Looks like Priya/Ahmed/Ravi swap reports, ask all three". X calls `GET /api/admin/audit?entity_type=flag_case` and sees `flag_case.parked` (`new_value.type = "ring"`, the note), then `flag_case.explanation_requested` with the three user ids. X warns Priya before she is asked, and tells her the case type and who else is in it, which D101 forbids members to learn. If X is a subject, X sees their own case from the first action on it. The `entity_id` (case id) can also be used with SEC5-L3.
- **Fix:** For `entity_type in {flag_case, appeal, decision_notice}`, and for `deed_points`/`deed` rows that carry a `case_id`, filter the rows with the same `_coi.visibility` used by the queue, or redact `reason`, `old_value` and `new_value` for conflicted rows. Alternatively, leave the case-linked entity types out of the audit view for conflicted admins. **PO decision:** §3.5 currently says conflicted admins may use the audit view.

### SEC5-M2 (Medium): several small cuts get round the second-admin rule

- **Location:** `backend/app/modules/scoring/_overrides.py:103-109` (`needs_second_admin` compares with `state.points`, the points in force now), `:280`.
- **Description:** Q77 says a cut to 0, or a cut of more than `max_cut_points` (10), needs a second admin. The code checks each override against the current active points, which already include earlier overrides. So one admin can make any cut in steps of 10 or less.
- **Exploit:** A deed earns 25. Admin A overrides to 15 (cut 10, applied at once), then to 5 (cut 10, applied), then to 1 (cut 4, applied). Only the final step to 0 would need a second admin, and 1 point is close to nothing. The same works after a declined or expired request: A lets it lapse, then cuts in steps. Each step creates a notice, but nobody reviews it unless the person appeals.
- **Fix:** Measure the cut against the rule result (`RoleState.rule_points`), not the active points, so the cut is cumulative: `needs_second_admin(rule_points, new)`. Also count it as a big cut when the new points are at most a small floor (for example `<= 1`). Alternatively, require a second admin when another lowering override for the same (deed, role) was written in the last N days.

### SEC5-M3 (Medium): raising points has no second admin and no notice

- **Location:** `backend/app/modules/scoring/_overrides.py:103-106` (`new >= old` → no request), `:215` (only a lowering emits `PointsOverridden`); `_ledger.py` `_overridden` keeps `counts_for_prize_rank`.
- **Description:** An override can set a doer's points to `override_max_points` (50) on any verified, counted deed. That replaces base, bonus and pair multiplier and **counts for prize rank**. No second admin is needed, and the person gets no notice. The only control is the 90-day COI rule, which an accomplice with no recent deed together passes. Two-step login for admins is not built yet (before-launch item), so a stolen admin session has the same power.
- **Exploit:** Admin A (or someone with A's stolen session) raises 20 deeds of accomplice C from 10 to 50 points in one month: +800 prize-rank points, enough to take a Kindness Hero title or a cash prize. It shows only in the audit log.
- **Fix (PO decision):** put raises under the same second-admin rule (a raise of more than `max_cut_points`, or any raise on a doer row that counts for prize rank), or cap raises at the rule result plus N. At least list recent raises on the admin status page.

### SEC5-M4 (Medium): revocation doesn't wait for co-subjects' appeals

- **Location:** `backend/app/modules/fraud/service.py:79-108` (`confirmed_case_for_user` checks only that user's notice), `scoring/_revoke.py:175-184, 207-211`.
- **Description:** D100 says a title or badge may be revoked "only when the decision is final for everyone on it", and that an overturned appeal "blocks title revocation for that case". The code checks only the title owner's notice. Another subject's appeal can still be open, or can later be overturned. An overturn releases the shared deeds for both people (`_appeals.py:450-462`, `_member.py:148-153` shows the owner "Restored after another review"). Revocation can't be undone: `revoked_*` is set once.
- **Exploit:** A reciprocal case about A and B is confirmed with `exclude`. A doesn't appeal; A's notice becomes final after 30 days. B appealed and waits for a second admin. An admin revokes A's November title (allowed). B's appeal is overturned: every shared deed is restored, and A's notices say "Restored after another review", but A's title stays revoked.
- **Fix:** In `confirmed_case_for_user` (and `case_standing`), also require that no notice of that case for a co-party of the user's adverse deeds has an open appeal, and that none of them was overturned for those deeds. Return `not_final` while any appeal is open.

### SEC5-M5 (Medium): ring detector cost and one case per overlapping cycle

- **Location:** `backend/app/modules/fraud/detectors/ring.py:103-123` (every simple path collected), `:141-162` (one `source.edges(..., match="doer")` query per member set, not counted against `max_search_edges`, plus one `Finding` per member set).
- **Description:** `max_search_edges` limits only the BFS loads. For each distinct member set found, the code runs one more unbounded query and adds one finding. Each finding becomes its own case, because `subject_key` is per member set.
  - **Cost:** a group of 60 accounts that all recognise each other (59 reports a month each, under `reporter_monthly_limit` 150) has 3,540 edges, below 5,000, so the search isn't truncated. It has about 32,000 member sets of size 3–5 through each new edge, so about 32,000 queries for every verified deed, and about 10⁸ queries for the group's month. The `fraud` handler jobs back up, and the D93 barrier waits for every fraud handler before the cutoff, so **month finalization stalls**. The nightly sweep does the same work in one transaction.
  - **Queue flooding:** a closed clique of 10 accounts that only recognise each other (internal share 100 %) opens C(10,3)+C(10,4)+C(10,5) = 582 ring cases plus 45 reciprocal cases. Each needs explanations and a decision. That buries real cases before the cutoff, or pushes admins to dismiss in bulk. A dismissal then protects those deeds for good (D81).
- **Fix:** Merge the cycles found from one trigger into one finding per strongly connected component, or per union of member sets that share the trigger edge. Compute the internal share with one batched read over the union of members. Count those reads against the budget, and cap the number of cycles evaluated (new config `ring.max_cycles`, recorded as `truncated`). Optionally, join an existing live ring case when the new member set overlaps it heavily.

### SEC5-M6 (Medium): ring search is truncated for busy users and can be evaded

- **Location:** `backend/app/modules/fraud/detectors/ring.py:89-101` (the BFS loads all out-edges of nodes up to 3 hops from the doer, with no pruning toward the reporter), `:129-137` (truncation flags nothing and only writes a log warning); `fraud/_detect.py:334-343` (per-deed runs record no `fraud_detector_runs` row).
- **Description:** With `max_size` 5 the search loads the out-edges of the doer's 3-hop out-neighbourhood. In a busy city graph that is far more than 5,000 edges: 150 reports by the doer, times active reporters at hop 2, and so on. Ring detection will often stop silently for well-connected users. An attacker can cause this on purpose: one ring member who also recognises many genuine, active users pushes every search through them over the budget, so the ring is never flagged. Per-deed truncation shows only in logs. The design promised "recorded as `truncated` (admin status page)", but only sweeps record it.
- **Exploit:** Ring Ravi → Kavya → Sneha → Ravi. Kavya also reports 100 active real users a month. Every check of a ring edge with Kavya as doer, or one hop from her, goes over 5,000 edges and flags nothing. The nightly sweep truncates in the same way.
- **Fix:** Use a bidirectional, depth-split search (forward from D for ⌈k/2⌉ hops and backward from R over in-edges for ⌊k/2⌋), which needs only paths that can close the cycle. Prune nodes outside the reverse reach of R. Count per-deed truncations, for example in a small `fraud_detector_runs`-like counter or the status endpoint, so admins can see the gap.

### SEC5-L1 (Low): appeal at the window edge races `finalize_decisions`

- **Location:** `backend/app/modules/fraud/_finalize.py:74-85` (`UPDATE … WHERE NOT EXISTS (appeal)`), `:117-150` (readiness uses `final_at` only); `_appeals.py:114`.
- **Description:** If a member's appeal locks the notice just before `appeal_until`, and the hourly job runs just after and commits later, the job's blocked UPDATE re-checks the row under READ COMMITTED but evaluates the `NOT EXISTS` subquery with its original snapshot. It sets `final_at` although an appeal exists. In the same transaction the readiness query sees `final=true, overturned=false` and emits `FlagDecisionFinal`. The deed is rejected while the appeal is open, and `confirmed_case_for_user` becomes true (revocation allowed). The window is a few milliseconds and only hurts the appellant, so this is not a practical attack.
- **Fix:** In readiness and in `confirmed_case_for_user`, treat a notice with an appeal whose `outcome IS NULL` as not final. Or take the notice rows `FOR UPDATE` in `finalize_decisions` before setting `final_at`.

### SEC5-L2 (Low): an overturned override appeal can restore nothing

- **Location:** `backend/app/modules/scoring/_overrides.py:548-561` (no-op unless the appealed override is still the one in force), `fraud/_member.py:144-147` ("Points restored after another review" on every overturn).
- **Description:**
  - **Raise after the cut:** admin 1 cuts 12 → 8 (notice). Admin 2 later raises 8 → 10 (no notice, M3). The appeal of the first notice is overturned, but the clear is skipped because the raise is in force. The member keeps 10 and is told the points were restored.
  - **Clearing a raise:** a raise 12 → 30 is cleared back to the rules. That counts as lowering (30 → 12), so it creates an appealable notice. Overturning it does nothing, because the clearing row has `points = null`.
- **Fix:** On an overturn, restore `previous_points` of the appealed override, or write the clear whenever the latest override was written after the appealed one. Show "Points restored" only when scoring actually changed something, or word it neutrally.

### SEC5-L3 (Low): `case_id` on overrides isn't validated

- **Location:** `backend/app/modules/scoring/_overrides.py:259-262`, `fraud/service.py:155-165`.
- **Description:** Any admin may attach any existing case id to an override. Only "unknown" is refused. The id isn't checked against the deed's people or the admin's case visibility. This gives a conflicted admin an oracle on case existence: with ids from the audit view (M1), the response is 422 `unknown_case` or not. It also lets the audit trail link an override to an unrelated case ("fraud_confirmed, case X") that a reader takes as justification.
- **Fix:** Require that the case is visible to the admin (`_coi`) and that the deed is a case deed, or at least that the deed's person is a subject; otherwise 422 `unknown_case`.

### SEC5-L4 (Low): revocation COI is person-level only, and the case is not tied to the title

- **Location:** `backend/app/modules/scoring/_revoke.py:87-96, 207-210`.
- **Description:** The revoking admin is checked only against the title owner (self, or a verified deed together in 90 days). An admin who is a subject or case-deed party of the cited case, but not directly related to this owner, passes. In a 4- or 5-person ring, members two steps apart have no edge together. The case also needn't concern the title's month or region: a March case can revoke a January title.
- **Fix:** Also require `fraud` case visibility for the admin (`_coi.is_conflicted` through a `fraud.service` read). **PO decision:** must the confirmed case contain deeds from the title's month (or before it)?

### SEC5-L5 (Low): member write limits share the admin bucket; reads are unlimited

- **Location:** `backend/app/modules/fraud/router.py:242-276`.
- **Description:** Member reply and appeal use `fraud.rate_admin_write` under the same key as admin writes (`fraud:write:u:{id}`). Abuse is bounded, because each request or notice accepts one reply or appeal and the database enforces it. But tightening the admin limit changes the member limit, and an admin's own reply counts against their admin budget. `GET /api/reviews` (several queries, polled by the Layout) and `POST /api/reviews/seen` have no limit, unlike the other member reads in `scoring` (`rate_me_user`).
- **Fix:** Add `rate_member_read` / `rate_member_write` to `FraudConfig` (new config version), with keys `reviews:read:u:` / `reviews:write:u:`, and apply them to all four member endpoints.

### SEC5-L6 (Low): `finalize_decisions` idempotency relies on the outbox

- **Location:** `backend/app/modules/fraud/_finalize.py:53-67, 89-106`.
- **Description:** "Already sent" is read back from `outbox_events` payloads, and every run reads every `reject` outcome of every confirmed case ever. Today nothing prunes the outbox and the deeds handler skips non-verified deeds, so a re-send is harmless. If an outbox retention job is added later (likely, given the volume), deeds will be re-sent every hour, and the scan grows without bound.
- **Fix:** Add a marker (for example `flag_case_deed_outcomes`-adjacent `rejection_sent_at` in a small insert-only table), or note in CLAUDE.md that `fraud.flag_decision_final` outbox rows must never be pruned. Limit the scan to outcomes without a marker.

### SEC5-L7 (Low): nightly sweep cost

- **Location:** `backend/app/modules/fraud/detectors/ring.py:183-202`, `_graph.py:273-294`, `_detect.py:404-415`.
- **Description:** The sweep loads `window + window_days` of verified edges (60 days) into memory. It then runs a full ring search for every edge in the window, inside one transaction. `MemoryEdges.edges` collects every matching edge of a frontier before applying `limit`, so the budget doesn't bound the work per call. At city scale this is roughly O(E × 5,000) Python operations in one long transaction holding the `fraud` worker. Combined with M5 it can run for hours.
- **Fix:** Sweep in chunks of trigger edges per transaction, stop collecting in `MemoryEdges.edges` at `limit`, add a time or edge budget per run (recorded as `truncated`), and run the sweep on its own worker (the queue is already separate).

---

## Checked and found fine

- **Authz:** every `/api/admin/fraud/*`, `/api/admin/audit` and new `/api/admin/scoring/*` endpoint uses `AdminUser` (401 signed out, 403 member/moderator); frontend admin routes sit behind `RequireRole(['admin'])`, nav links hidden for moderators.
- **Conflict 404s:** case detail, park, unpark, explanations and decision all go through `_actions.visible_case` (404 `case_not_found`). The queue, counts and status counts use the same `Visibility`. Appeals exclude the appellant, the case decider (`resolved_by`), the override actor and requester (from the notice audit row), subjects and related admins, in the list, the counts, the detail and the decision. Override requests exclude the requester (service, plus DB CHECK `decided_by <> requested_by`) and related admins. #9, #13, #14, #15 and #16 return 404 for own or related people. Input validation (note, reason code, points) runs before the lookup, so a 422 is no existence oracle.
- **IDOR:** `/api/reviews` items are filtered by `user_id` in SQL (`_requests_of`, `_notices_of`); reply and appeal lock their row with `user_id` in the WHERE (404 otherwise). Member items show only the caller's own deeds (`_mine`), with no case id, type, detector, admin or other subject beyond the other party on each own deed.
- **Integrity:** holds are written only by `decide` (confirm or dismiss) and `decide_appeal` (overturn). `raise_flag` holds only if `hold_on_open` is true (all false in config). Deed status is changed only by `deeds/_reject.py` from `FlagDecisionFinal`. The ledger is written only through `reconcile_deed`. `DeedHoldChanged.held` is never trusted (scoring re-reads `is_held`). Confirm needs an action for every case deed and nothing else, and an all-release confirm is refused.
- **Database guards:** append-only and set-once triggers (`flag_case_decisions`, `flag_case_deed_outcomes`, `score_overrides`, `flag_cases_guard`, `forbid_except_set_once` for holds, replies, `final_at` and appeal decisions, `override_requests_decide_once`) match the service writes. The order "holds, outcomes and notices before resolving the case" is respected. `replied_at <= reply_due_at` and `reviewer_id <> user_id` are CHECKs.
- **Races:** a double confirm or decision is blocked by the case row `FOR UPDATE` and a status re-check (`populate_existing`). A deed that joins during a confirm gives 422 `deed_actions_invalid`. Concurrent appeals hit the notice `FOR UPDATE` and `uq_appeals_notice_id`. Double appeal decisions hit the appeal row `FOR UPDATE`. Approve against expire uses the request row lock (the expire UPDATE re-checks `pending`). Concurrent big-cut requests are serialized by the deed day locks and the partial unique index. `finalize_decisions` uses an advisory xact lock plus `queueing_lock`.
- **Finalization barrier (D93):** fraud handlers before the cutoff, plus unscored hold events since the month start, any time. Rejections are already held, so the deeds hop doesn't need to be in the barrier. Featured City reads final months as of `finalized_at` (D94).
- **Privacy (D95/D101):** no coordinates, phone numbers, device or IP hashes in fraud tables, responses, events or logs. `pair_signals` compares `device_hash`/`ip_hash` in SQL and returns only booleans. Event payloads are ids, codes and numbers. The owner ledger hides prize-rank-only pairs, and `PointsChanged.notify=false` for them. Member wording comes from config labels. The two new log lines carry no personal data. The `audit_log` rows written in M1–M5 hold no secrets, phone numbers or coordinates.
- **Content cases** have both doer and reporter as subjects, so the reporter is asked and can appeal before losing points.
- **Frontend:** no secrets. The only `dangerouslySetInnerHTML` is the pre-existing server-made QR SVG (M1). Server text (notes, replies, reasons) renders as React text. Links use ids with `encodeURIComponent`.

## For the PO

1. **M3:** should raising points also need a second admin (or be capped at the rule result + N)? Today one admin can add up to 50 prize-rank points per deed.
2. **M2:** measure "big cut" against the rule result (cumulative), so small steps can't add up to a big cut?
3. **M1:** may conflicted admins see case-linked audit rows (notes, subject ids)? Recommended: filter them like the queue.
4. **M4 / L4:** revocation should wait until no co-subject's appeal on the same deeds is open or overturned (as D100 says). Must the cited case also cover the title's month?
5. **M5:** one ring case per connected group of colluders instead of one per 3–5-person cycle?
6. **L5:** add separate member rate limits to the `fraud` config (new config version)?
