# M5 Fraud & Review: design

**Status:** Revision 4 **approved by the PO on 2026-11-26**. This is the **baseline** fraud and review system, not a final strategy: detectors, rules and review steps are expected to be added, improved or replaced in later milestones (D102). Revisions 2–4 apply the PO's three principles (§0.0) and record the PO answers to Q59–Q78 in the decision log (§11). Every threshold is a config row (one new version changes it).
**Baseline:** `main` at af5d00f (M1–M4 approved; M4a and D71–D76 merged, migrations up to 0025). Branch `m5-fraud`.
**Sources used:** PRD §5 (anti-collusion measures, invite safeguards, invites off, edit and withdraw), §6.5 (feed: "withdrawn, rejected, flagged or moderator-removed deeds disappear from the feed"), §6.9, §6.14 (flagged-cases metric), §8 (FlagCase, ScoreOverride / AuditLog, ImpactEvaluation status, PointsLedger), §9 (reporter points never in prize rank, title revocation for confirmed fraud, finalization, badges revocable for fraud, "flagged scores go to a review queue"), §10 steps 4 and 7, §11 module table (Fraud Checks, Review & Audit), §12 (prize eligibility "No open flag cases", limits table, open item 9); ROADMAP M5; `docs/M4-DESIGN.md` (D47–D52, D55, D57, D59, D64, D67, D70, D72, A3, A5, §4.2, §4.6, SEC3 L6/L7); `docs/M3-DESIGN.md` (D42, D43, D45, §3.4a, §3.5: report cases, append-only decisions, conflict of interest); `docs/M2-DESIGN.md` (D6, D13, D17, D21, §3.4/§3.4b prize-rank upgrade, security notes on M5); the code: `backend/app/modules/scoring/` (`_ledger.py`, `_leaderboard.py`, `_finalize.py`, `handlers.py`, `jobs.py`, `models.py`, `events.py`, `_labels.py`, `_reads.py`), `deeds/service.py`, `deeds/events.py`, `deeds/models.py`, `media/_admin.py`, `feed/_moderation.py`, `identity/service.py` (`ReviewerUser`, `AdminUser`), `core/audit.py`, `core/events.py`, `core/runtime_config.py`, `modules/registry.py`, `tests/test_architecture.py`, `frontend/src/features/admin/`, `frontend/src/app/routes.tsx`.

Decision numbers continue from M4 (D77 onwards) and question numbers from Q59.

---

## 0. Decisions

### 0.0 PO principles (2026-11-26)

These override any decision below that conflicts with them.

1. **Encourage first, investigate second.** Automated suspicion must not discourage anyone from doing good deeds. A detector only puts a case in the admin queue: it changes no points, no rank, no feed post and no profile, and nobody is told (D80, D98).
2. **Suspicion is not fraud.** Detectors find unusual patterns. Only an admin can confirm a case, and every effect on a person comes from an admin action with a written note (D88, D98).
3. **Protect genuine contributors.** Before an admin confirms anything against someone, that person is asked about their deeds and can explain (D99). After a decision, they can appeal once to a different admin. Effects that can't be undone (rejecting a deed, revoking a title or badge) wait until the appeal window has closed or the appeal is decided (D100). Members never see the word "fraud", other people in the case or how the detectors work (D101).

### 0.1 Architecture decisions

| # | Decision | Why | PRD ref |
| --- | -------- | --- | ------- |
| D77 | **One new module, `fraud`** (`backend/app/modules/fraud/`). It owns the detectors (reciprocal, ring), flag cases, prize holds, the admin review queue and the audit-log view. The PRD's "Fraud Checks" and "Review & Audit" rows become one module, like Deed Submission and Verification became `deeds` (PRD §12.7). | A case, its deeds and the holds a decision places must be written in one transaction; splitting them would need an event hop with no benefit. Score overrides and title/badge revocation stay in `scoring`, which owns the ledger, titles and badges. | §11, ROADMAP M5 |
| D78 | **Imports.** `fraud` may import `app.core`, `deeds.service`, `identity.service`, and from `scoring.service` **only the event classes** `ReporterConcentrationExceeded`, `DeedNeedsReview` and `PointsOverridden`. `fraud.service` imports nothing from `scoring`. `scoring` may import from `fraud.service` only `DeedHoldChanged`, `AppealDecided`, `held_deed_ids`, `is_held` and `confirmed_case_for_user`. `deeds` may import from `fraud.service` only `FlagDecisionFinal` (in `deeds/handlers.py`). Nobody imports `fraud` from `feed` or `media`, and `fraud` never imports `feed` or `media`. All enforced by allow-lists in `tests/test_architecture.py`, like D47's `DeedPhotoReady`. | Keeps service files acyclic (`fraud.service` → `deeds.service`, `identity.service`; `scoring` → `fraud.service`). Feed visibility and photo state stay out of every fraud and points decision. | §11 |
| D79 | **A flag never edits a deed or a ledger row. A prize hold is placed only when an admin confirms a case** (D80, D88). `fraud.prize_holds` holds one row per (deed, case). A deed is *held* while any of its holds is unreleased. `scoring.reconcile_deed` computes a doer entry's `counts_for_prize_rank` as `deeds.prize_rank_eligible AND NOT fraud.is_held(deed)`. Placing or releasing a hold emits `DeedHoldChanged`; scoring's handler calls `reconcile_deed`, which writes the usual reversal + new award with `counts_for_prize_rank` flipped (reason `prize_rank_changed`, points unchanged). `deeds.prize_rank_eligible` keeps its M2 meaning (invite rule) and gets no second writer. | Reuses the M4 path that already handles `DeedPrizeRankChanged` at any time, append-only (D48, D49). Points the user earned are untouched: a hold only takes the deed off boards and prize rank, which is exactly PRD open item 9. | §12 item 9, §10.7 |
| D80 | **Opening a case holds nothing** (PO principle 1, Q60). `fraud.hold_on_open` stays a config key with every type `false`. Holds come **only from a confirmed decision**: confirm with `exclude` or `reject` (D88). There is no hold before a decision, not even by an admin (PO Q73). A hold is released by an appeal that is overturned (D100) and becomes moot when a deed is rejected. | The PRD item 9 says flagged deeds are held until decided. That would let an algorithm take honest people off the board, which principles 1 and 2 forbid. Only a confirmed decision, which the person could explain first and can appeal, takes a deed off the board. | §12 item 9 (changed by PO) |
| D81 | **One live case per (type, subject set).** A case's subjects are the users involved (a pair for reciprocal, the cycle members for a ring, doer + reporter for concentration, the doer for content). `subject_key` = SHA-256 of type + sorted user ids. A partial unique index allows one `open` or `parked` case per key; a new matching deed **joins** that case (nothing is held while a case is open). After a case is **dismissed**, its deeds are never flagged again for the same key; only deeds verified after the dismissal can open a new case. | Same pattern as M3 report cases (one open case per photo/comment). Stops the queue from filling with duplicates and stops re-flagging what an admin already cleared. | §8 FlagCase |
| D82 | **Detectors run as Procrastinate jobs, idempotent and re-runnable.** (1) Per deed: `fraud.handlers.check_verified_deed` subscribes to `DeedVerified` (a `core.handle_event` job) and runs the reciprocal and ring checks for that one new edge. (2) Nightly `fraud.sweep` (02:30 IST) re-runs both over the whole window, catching anything missed after a config change or an outage. (3) CLI `python -m app.modules.fraud.cli sweep --detector reciprocal|ring --since YYYY-MM-DD` for manual re-runs (no HTTP endpoint). Every write is insert-or-join with `ON CONFLICT DO NOTHING`, so running twice changes nothing. Each sweep writes a `fraud_detector_runs` row. | PRD §11: fraud checks run as background jobs. Event-driven checks catch most cases within seconds of verification (before month end); the sweep is the safety net. | §10.4, §11 |
| D83 | **Reciprocal rule.** When a deed A→B (reporter A, doer B) is verified, and B reported A in a deed verified within `reciprocal.window_days` before it, with at least `reciprocal.min_deeds_each_way` verified deeds in each direction inside that window, open or join a `reciprocal` case for {A, B}. Case deeds: every verified deed between A and B (both directions) verified in the window. Only `verified` deeds count; withdrawn ones don't. Seed: 30 days, 1 each way (Q59). | PRD §5: "A verifies B and B verifies A within a short period". | §5 |
| D84 | **Ring rule (graph cycles over a recent window).** Graph: an edge R→D for each pair with at least `ring.min_edge_deeds` verified deeds in the last `ring.window_days`. When R→D is verified, search simple paths D→…→R of 2 to `ring.max_size − 1` edges (cycles of `ring.min_size`..`ring.max_size` people; 2-person cycles are reciprocal, D83). A cycle becomes a `ring` case only if the members "mostly verify each other": deeds received by members from other members ≥ `ring.min_internal_share_pct` of all verified deeds the members received in the window. Case deeds: the deeds on the cycle's edges in the window. The search is breadth-first with at most `ring.max_search_edges` edges loaded; on hitting it the run is recorded as `truncated` (admin status page), nothing is flagged from a partial search. Seed: 30 days, sizes 3–5, 1 deed per edge, 50 %, 5,000 edges (Q62). | PRD §5: "small groups who mostly verify each other (A→B→C→A)". The internal-share test is the "mostly" part; without it every 3-person friend triangle is a case. | §5 |
| D85 | **Reporter concentration lives in `scoring`**, because it is defined on prize-rank **points**, which only `scoring` has. Job `scoring.check_concentration` (every 15 min, open months only): per doer and month, over doer ledger rows with `counts_for_prize_rank`, the share of points from each reporter (`counterpart_user_id`). If the doer has at least `concentration.min_doer_points` prize-rank points that month and one reporter's share exceeds `concentration.max_reporter_share_pct`, scoring records it once in `concentration_signals` (unique per doer, reporter, month) and emits `ReporterConcentrationExceeded` with that reporter's deed ids for the doer that month (bounded by the pair limit of 15). `fraud` opens a `concentration` case. **M5 does not cap boards**: the hard 30 % cap is applied in M8's prize calculation (Q61). Seed: 30 %, 50 points. | PRD §5 and §12. Capping every board now would change approved M4 boards and collides with A5 (three equal reporters are 33 % each). Cash is where the rule matters, and that is M8. | §5, §12, M4 A5 |
| D86 | **Content cases from the M4 text rules.** When `reconcile_deed` writes a rule-based evaluation with `human_review_required` (text check `spam_or_empty`, D55), scoring emits `DeedNeedsReview(deed_id, reason="spam_or_empty")`; `fraud` opens a `content` case for the doer. No hold (D80). The admin can dismiss it, or raise the points with an override (D91) if the rules were wrong. Can be switched off with `detectors_enabled.content` (Q71). | D55 said the review flag is "read by an M5 queue"; PRD §9 "flagged scores go to a review queue". AI confidence flags stay out: the AI is shadow-only and off (D61–D63). | §9, M4 D55 |
| D87 | **Invite anomalies: the case type exists, the detector is not built in M5** (Q63). Invites are off by config (D17), so no `invite_accept` deed can be verified and a detector would only run in tests. Before `deeds.invites_enabled` may be set to true, three things must ship together: the invite-anomaly detector (rules sketched in §4.5), the human review that lets an invite deed count for prize rank (PRD §5), and the SEC3 L7 fix. Recorded in ROADMAP as a precondition for switching invites on. | Simplest design that satisfies the PRD while invites are off; avoids building and maintaining dormant code against assumptions that may change when invites return. | §5, M2 D17, M4 SEC3 L7 |
| D88 | **Review actions are append-only.** A case moves `open` ⇄ `parked` → `resolved` (PRD statuses). Resolving sets `outcome` = `confirmed` or `dismissed`. Every action writes a `flag_case_decisions` row (append-only, note required) and an `audit_log` row (`entity_type="flag_case"`). The actions are park, unpark, ask for explanation, confirm and dismiss. On **confirm**, the admin picks per deed: `exclude` (stays verified and keeps its points; out of prize rank and boards), `reject` (deed rejected once the decision is final, D89, D100) or `release` (this deed was fine). The UI defaults every deed to `release`, so an admin must choose an adverse outcome deliberately, and it marks deeds that someone outside the case also reported. Confirming with an adverse outcome for a person needs their explanation step first (D99). On **dismiss**, every hold of the case is released. Per-deed outcomes go to `flag_case_deed_outcomes` (append-only). A resolved case is never reopened; an appeal (D100) or new evidence (a new case, D81) is the only way forward. Open and parked cases hold nothing. | Mirrors M3 D42/D45 (decisions table + audit + required note). Per-deed outcomes matter because a ring case can mix colluding and genuine deeds. The `release` default follows principle 3. | §8, §12 item 9 |
| D89 | **Rejecting a deed goes through `deeds`, and only once the decision is final** (D100): the appeal window has closed for both people on the deed, or their appeals were upheld. Until then the deed is excluded (held), not rejected. `fraud` then emits `FlagDecisionFinal` with `rejected_deed_ids`; `deeds/handlers.py` calls a new `_reject.reject_for_fraud(deed_id, case_id)`: deed status `verified` → `rejected`, a `verification_events` row (`kind='rejected'`), same-act group counters updated, the invite prize-rank downgrade of M2 §3.4b reused when `distinct_reporters` drops, and `DeedRejected(new_status="rejected", reason="fraud_confirmed")` emitted. **No promotion** of the next report in the same act. Existing handlers then do the rest: `feed` removes the post (D37), `scoring` reverses doer and reporter points (D49). Idempotent: a deed not `verified` any more is skipped. Lock order: `same_act` advisory → deed row. | Only `deeds` writes deed status (CLAUDE.md boundary rule). No promotion: in a confirmed fraud case, the other reports of the act are suspect too, and a promotion after the window would move points in a closed period. | §6.5, §10.7 |
| D90 | **Roles and conflict of interest.** Fraud cases, score overrides, title/badge revocation and the audit view are **admin-only** (`identity.service.AdminUser`; moderators get 403). An admin is **conflicted** for a case if they are a subject, the reporter or doer of any case deed, or have any verified deed with a subject (either role) in the last `coi_lookback_days` (seed 90). Conflicted admins don't see the case in the queue and get **404** on its detail and actions (not 403), so a flagged admin can't learn that they are flagged. The same rule applies to overrides (own deeds and deeds with a related user → 404) and revocations (own titles/badges → 404). | PRD §6.5 Q34: later admin areas are admin-only; M3: nobody decides a case they are involved in. The lookback catches an admin who is part of the friend group being reviewed. 404 follows the M2/M4 non-owner rule (M4 #9). | §6.5 (Q34), §8 |
| D91 | **Score overrides with a required reason**, in `scoring`. Table `score_overrides` (append-only): the latest row per (deed, role) is in force. `points` sets the deed's points for that role (0 to `override_max_points`); `points = null` clears the override (back to the rules). `reconcile_deed` uses the override instead of the rule result for an **award** target: the override replaces base + bonus + pair multiplier, while status, `counts_for_points`, the daily cap and the prize hold still apply. Override to 0 writes a zero-point `decision` row (`deed_units = 0`), so the deed also stops counting as a counted deed. If the role has no award target (deed not verified, not counted, or capped) → 409 `nothing_to_override`. Reason: `reason_code` from config (`fake_photo`, `rule_misfire`, `fraud_confirmed`, `other`) plus a free-text `note` (min `note_min_chars`). Every override writes `audit_log` (`entity_type="deed_points"`, old/new points). Ledger reason `admin_override`. Allowed any time; after finalization the ledger changes but final boards don't (D93). An override that **lowers** points emits `PointsOverridden`; `fraud` turns it into a decision notice the person can appeal (D100). Raising points sends no notice. **Big cuts need a second admin** (PO Q77): an override to 0, or one lowering points by more than `override_second_admin.max_cut_points` (seed 10), is stored as a pending `override_requests` row. It has no effect until a different, non-conflicted admin approves it (endpoint #18), and then it is applied and audited as above. A decline is recorded with a note and nothing changes. Requests expire after `override_second_admin.expire_days` (seed 14). | PRD §6.9 "admin overrides and recorded reasons", §8 ScoreOverride, §10.7. Fixes the fake-photo bonus (M4 A3) and spam misfires. Going through `reconcile_deed` keeps one writer of the ledger (D49) and keeps overrides re-applicable on any later re-score. | §6.9, §8, §10.7 |
| D92 | **Revoking titles and badges needs a confirmed case.** `POST /api/admin/scoring/titles/{id}/revoke` and `/badges/{id}/revoke` take `{case_id, note}`; scoring checks with `fraud.service.confirmed_case_for_user(case_id, user_id)` that the case is resolved `confirmed`, the user is a subject with an adverse outcome, and the decision is **final for that user** (D100: appeal window closed or appeal upheld). Until then the button explains when revocation becomes possible. Sets `revoked_at`, `revoked_reason`, `revoked_by` once (M4 columns), writes `audit_log`, emits `HeroTitleRevoked` / `BadgeRevoked`. A revoked title is **not passed to the next person** and the final board is not recomputed (Q67). Reads already filter `revoked_at IS NULL`. | PRD §9: "Admins can revoke a title for confirmed fraud"; badges "permanent unless revoked for fraud". | §9 |
| D93 | **Finalization and fraud.** (a) **Before final:** a hold from a confirmed decision flips the doer row's prize flag, so the next board refresh (≤ 5 min) drops the deed; released holds put it back. (b) **Barrier:** `finalize_month` also waits until (i) every `DeedVerified` event before the cutoff has been processed by the fraud handler (add `app.modules.fraud.` handler names to the barrier list), and (ii) no `fraud.deed_hold_changed` event of any time since the month start is still unprocessed by scoring. So a hold placed before finalization is always in the final board. (c) **Finalization does not wait for review decisions:** a deed still held at finalization is out of that month's final board; dismissing the case later releases the hold in the ledger, but the final board and titles stay frozen (D64). (d) **After final:** new holds, overrides and rejections write ledger rows dated in the final month; final snapshots and titles never change; only an admin revocation (D92) removes a title. The queue sorts open cases by `review_by` (the earliest month-end of its deeds + 24 h) so admins can decide before the cutoff. An undecided case never affects a final board (principle 1). | Keeps D64's promise (a final month never moves) and closes the outbox gap for the new event hop. Waiting for human decisions would let one slow case block every board. | §9 Finalization, §12 item 9 |
| D94 | **Featured City reads final months as frozen.** `featured_city_rows` (D72) is computed on read from the ledger. For a finalized month it reads only ledger rows created at or before `month_finalizations.finalized_at`, so a post-final hold or override can't move it while the boards stay frozen. Open months stay live. | Consistency with D64 now that ledger rows can change after finalization. | M4 D72 |
| D95 | **Flags are private.** Nothing public shows a case, a hold, a detector or a flag: no public field, no profile marker. A member learns of a case only when an admin asks them for an explanation or tells them a decision (D99–D101), and then sees only their own deeds (Q64). Event payloads carry ids, types and numbers only. The owner's points history (`GET /api/scoring/me/ledger`) omits reversal + award pairs whose only change is `counts_for_prize_rank` (net zero points), so a hold or release isn't visible as "Leaderboard eligibility changed". `PointsChanged` with `delta = 0` and reason `prize_rank_changed` is marked so M6 never notifies it. The board rank change itself is visible, as for any deed. Case detail for admins contains no coordinates, phone numbers, device or IP hashes: only derived booleans (`shared_device`, `shared_network`) per pair. | PRD §6.5 privacy rules; a false positive must not become a public accusation. | §6.5, §8 |
| D96 | **Audit view.** `GET /api/admin/audit` (admin-only, filters, keyset cursor) reads `audit_log`, which already holds role changes, config versions, prize-rank flips, photo/comment decisions and, from M5, case actions, overrides and revocations. Read-only; it lives in the `fraud` router (the PRD's "Review & Audit"). | PRD §8 ScoreOverride/AuditLog, ROADMAP "audit view". Reuses `app.core.audit`, no new log. | §8, ROADMAP M5 |
| D97 | **All thresholds are config rows** in a new namespace `fraud`, typed by `fraud/_limits.py:FraudConfig` with no defaults (§2.4), seeded by migration 0026. Scoring reads the concentration rule through `fraud.service.concentration_rule(session)` so it lives in one place. No new keys in `scoring` or `leaderboard`. | CLAUDE.md config rule; PRD §12 lists the 30 %. | §12 |
| D98 | **Detection opens a case and does nothing else** (principles 1, 2). No hold, no feed change, no member notice, no rank change, no effect on titles at finalization. The admin queue is the only place a case shows. A case nobody has looked at by the cutoff leaves that month's final board as it is; if fraud is confirmed later, D92 applies. | PO principles. The cost, that a colluder may keep a title when review is slow, is accepted and limited by the queue deadline and later revocation. Admins can't hold deeds before deciding (PO Q73). | PO 2026-11-26 |
| D99 | **Explain before a decision.** Before confirming a case with an adverse outcome (`exclude` or `reject`) for a person, the admin uses **"Ask for an explanation"** (endpoint #10) for them. That person then sees a neutral request under *Profile → Reviews*: "We're taking a closer look at some of your recent deeds. If you'd like, tell us about them." It lists their own deeds in the case (category, date, area, the other person on that deed) and has an optional reply of up to `explanation.reply_max_chars`, due in `explanation.reply_days` (seed 14). Confirming with an adverse outcome for that person returns 409 `explanation_pending` until they have replied or the due date has passed, and 409 `explanation_required` if they were never asked. Dismissing never needs it. The admin sees the reply on the case page. Requests are in-app only until M6 adds notifications. | Principle 3. A neutral message that names nobody else and no detector limits what a colluder learns. | PO 2026-11-26 |
| D100 | **Decision notices and one appeal.** If no other eligible admin exists, the appeal waits and permanent steps stay paused (PO Q78). When a case is confirmed with an adverse outcome for a person, or an admin lowers someone's points with an override (D91), that person gets a **decision notice** under *Profile → Reviews*. It lists their affected deeds, what happened to each ("Not counted for leaderboards", "Will be removed", "Points changed from 12 to 8") and a neutral reason label (D101). They can **ask for another review** once per notice within `appeal.window_days` (seed 30), with a written reason. An appeal goes to an admin who is not the one who decided and is not conflicted (D90). They choose **uphold** or **overturn**, with a note; that answer is final and shown in the notice. Overturn releases the person's holds, cancels pending rejections, clears the override (a new `score_overrides` row with `points = null`) and blocks title revocation for that case. **Effects that can't be undone wait:** a deed is rejected (D89) and a title or badge can be revoked (D92) only when the decision is final for everyone on it. That means the window closed with no appeal, or the appeal was upheld. Exclusion (a hold) and an override take effect at once, because both can be reversed. Job `fraud.finalize_decisions` runs hourly and emits `FlagDecisionFinal`. | Principle 3. Delaying the permanent steps means a successful appeal can always restore everything. | PO 2026-11-26 |
| D101 | **Member wording.** Members never see "fraud", "flag", "case", "ring", detector names or other people in the case. Reason labels come from config (`member_reason_labels`). Case types use "These recognitions didn't meet our rule that recognitions should come from independent witnesses". Override reason codes use, for example, `fake_photo` → "The photo didn't show this deed" and `rule_misfire` → "Points corrected". A rejected deed shows "Removed after review" in My deeds and links to its notice. Admin screens may say "fraud". | Principle 3 and privacy. | PO 2026-11-26 |
| D102 | **Built to be replaced in parts** (PO 2026-11-26: M5 is the initial approach). (1) **Detectors are plug-ins:** each implements one `Detector` protocol in `fraud/detectors/` (`name`, `case_type`, `on_event(session, event) -> list[Finding]`, `sweep(session, window) -> list[Finding]`) and is listed in one registry. `Finding` = (case type, subject user ids, deed ids, signal numbers). Adding, improving or removing a detector touches only its file, the registry and its config key; nothing in cases, holds, review, notices, appeals or scoring changes. (2) **Case types are data:** a type is a config entry (enabled, member reason label) plus a detector; the case/hold/decision tables don't depend on which detector opened a case (the `type` enum gets a value per new type through a small migration). (3) **Effects go through one path:** every effect on points or boards is a prize hold or an override, applied by `scoring.reconcile_deed`. A new verification process or a better rule changes the inputs, not the ledger. (4) **Review workflow steps are separate services** (`_review.py` decide, `_explain.py`, `_appeals.py`, `_notices.py`), each behind `fraud.service` functions and config, so a step can be changed or replaced without touching detectors. (5) **Other modules see only `fraud.service`** (D78), so the whole module can be reworked behind it. (6) Every rule number is config (D97). | The PO expects better detection and verification methods as the platform grows. | PO 2026-11-26 |

### 0.2 PRD rules that are ambiguous or conflict with built behaviour

| # | PRD rule | Problem | Resolution in this draft |
| --- | -------- | ------- | ------------------------ |
| A13 | §6.5 "Withdrawn, rejected, **flagged** or moderator-removed deeds disappear from the feed." | Hiding a deed as soon as a detector fires is a public signal to the doer, the reporter and their followers, and reciprocal flags will often be friends doing real good. | Open flags never touch the feed; only deeds **rejected** on confirmation leave it (through the existing `DeedRejected` feed handler). Q68. |
| A14 | §12 item 9 "flagged deeds are held out of prize rank until this is decided" | An automatic hold lets an algorithm take honest people off the board (PO principles 1 and 2). | **Changed by PO:** no automatic hold (D80, D98). An admin may hold a case while reviewing it. For M8 cash prizes, an open case **delays** a payout until the case is decided and never denies it. Q60. |
| A20 | §10.7 and §9: rejection and revocation for fraud happen when an admin decides | Silent on explanation and appeal. | **PO principle 3:** explain first (D99), one appeal, and permanent effects wait for it (D100). Q64, Q65. |
| A15 | §5 30 % reporter concentration vs §12 "at least 3 different reporters" for prizes (M4 A5) | Three equal reporters give 33 % each; and a doer with 1–3 deeds is always over 30 %. | D85: a minimum volume (`min_doer_points`), flag-only in M5, hard cap in M8's prize calculation. Q61. |
| A16 | §5 "an invite-verified deed … counts toward prize rank only after a second independent report … or a human review" | Human review of invite deeds is M5-shaped, but invites are off (D17). | Deferred with the invite-anomaly detector (D87). Q63. |
| A17 | §8 ImpactEvaluation `status (proposed / approved / overridden)` | M4 left `proposed` only and said M5 adds the others. | Not added: `score_overrides` is the record of an override and is linked from the ledger row (`breakdown.override_id`). `impact_evaluations` stays a record of what the rules (and later the AI) said. |
| A18 | §5 reciprocal "within a short period"; ring "small groups who mostly verify each other" | No numbers. | D83, D84 with config seeds; Q59, Q62. |
| A19 | §9 "Admins can revoke a title for confirmed fraud" | Silent on whether the next person gets it and on the final board. | D92/D93: not passed down, board frozen. Q67, Q72. |

### 0.3 Out of scope for M5 (pushed back)

- AI-based fraud detection or scoring of suspicion (M4b AI stays shadow and off).
- A graph database or a separate fraud service; the graph is small and built per check from Postgres rows.
- Suspending or blocking accounts (M7 Moderation). A confirmed case can be referred to the `set-role`/support process; no new account-status endpoint.
- Push, email or SMS notifications about explanation requests and decisions (M6). M5 shows them in-app under *Profile → Reviews*, with a dot on the Profile tab.
- Prize eligibility checks (M8): `fraud.service.open_case_user_ids(user_ids)` is provided for it now; an open case delays a payout, never denies it (A14).
- An admin config editor: thresholds change by publishing a config version (data migration or `python -m app.core.cli`), as today.
- Manual case creation by admins. Overrides are available from any deed's admin page without a case.

---

## 1. Scope

### In (PRD references)

- Detectors: reciprocal (§5), rings as graph cycles over a recent window (§5), reporter concentration 30 % (§5, §12), content review from the M4 text rules (§9). Invite anomalies: case type only, detector deferred (D87).
- `FlagCase` with types `reciprocal`, `ring`, `concentration`, `invite_anomaly`, `content` and statuses `open`, `parked`, `resolved` (§8).
- Prize holds placed only when an admin confirms a case (`exclude` or `reject`), through the M4 ledger (§12 item 9 as changed by the PO).
- Admin review queue with park / ask for explanation / confirm / dismiss and per-deed outcomes; rejection of confirmed fraudulent deeds once the decision is final (§10.7).
- Member *Reviews* page: explanation requests with a reply, decision notices and one appeal each, decided by a different admin (PO principle 3).
- Score overrides with a required reason (§6.9, §8, §10.7).
- Revocation of Kindness Hero titles and badges for confirmed fraud (§9).
- Audit-log view (ROADMAP M5).
- Finalization barrier extended to the new event hop (§9 Finalization).

### Out

See §0.3. Also: M6 notifications for any of the above, M8 prize rules (they will read `open_case_user_ids` and apply the 30 % cap).

---

## 2. Data model (database-specialist)

Migrations: **0026 `fraud`** (fraud tables + `fraud` config v1) and **0027 `scoring_overrides`** (scoring tables and enum values). FKs to `users` and `deeds` use `RESTRICT`. No table stores coordinates, phone numbers, device or IP hashes, or descriptions.

### 2.1 `fraud` module (`fraud/models.py`, migration 0026)

**`flag_cases`**

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | uuid PK | |
| type | enum `reciprocal`, `ring`, `concentration`, `invite_anomaly`, `content` | |
| status | enum `open`, `parked`, `resolved` | |
| outcome | enum `confirmed`, `dismissed` null | |
| subject_key | char(64) | SHA-256 hex of `type` + sorted subject ids (D81) |
| signal | jsonb | numbers and codes only, per type: reciprocal `{a_to_b, b_to_a, first_gap_hours}`; ring `{size, internal_share_pct, edges}`; concentration `{period_month, share_bp, doer_points}`; content `{text_check}` |
| detector_config_version | int | `fraud` config version that opened it |
| review_by | timestamptz null | earliest (IST month end + 24 h) of its deeds' months that are not yet final; null when all are final |
| deed_count, held_count | int | maintained with the case row lock |
| opened_at | timestamptz | |
| updated_at | timestamptz | last join or action |
| resolved_at | timestamptz null | |
| resolved_by | uuid null FK users | |

Constraints: `uq_flag_cases_live UNIQUE (type, subject_key) WHERE status IN ('open','parked')`; CHECK `(status = 'resolved') = (outcome IS NOT NULL AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)`. A trigger forbids changing `type`, `subject_key` and any change once `status = 'resolved'`.
Indexes: `(status, review_by NULLS LAST, opened_at)` (queue order); `(type, status)`.

**`flag_case_users`**: `case_id` FK, `user_id` FK users; PK `(case_id, user_id)`; index `(user_id)`. Insert-only (trigger).

**`flag_case_deeds`**: `case_id` FK, `deed_id` FK deeds, `added_at`; PK `(case_id, deed_id)`; index `(deed_id)`. Insert-only.

**`prize_holds`**

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | uuid PK | |
| deed_id | uuid FK deeds | |
| case_id | uuid FK flag_cases | |
| held_at | timestamptz | |
| released_at | timestamptz null | set once |
| release_reason | enum `released_on_confirm`, `appeal_overturned` null | |

Constraints: `UNIQUE (deed_id, case_id)`; CHECK `(released_at IS NULL) = (release_reason IS NULL)`; trigger: only `released_at`/`release_reason` may change, once. Partial index `(deed_id) WHERE released_at IS NULL` (the hot `is_held` read).

**`flag_case_decisions`** (append-only, `forbid_modification()`): `id`, `case_id`, `action` enum `park`, `unpark`, `request_explanation`, `confirm`, `dismiss`, `actor_id` FK users, `note` text CHECK `length(btrim(note)) >= 5` (service enforces `note_min_chars`), `created_at`.

**`flag_case_deed_outcomes`** (append-only): `case_id`, `deed_id`, `decision_id` FK flag_case_decisions, `outcome` enum `exclude`, `reject`, `release`, `created_at`; PK `(case_id, deed_id)`.

**`explanation_requests`**: `id`, `case_id` FK, `user_id` FK users, `requested_by` FK users, `requested_at`, `reply_due_at`, `reply` text null (CHECK length ≤ 2000; the service enforces `reply_max_chars`), `replied_at` null. `UNIQUE (case_id, user_id)`. The trigger allows only `reply` and `replied_at` to change, once, and only before `reply_due_at`.

**`decision_notices`** (one per person per adverse decision): `id`, `user_id` FK users, `kind` enum `case`, `override`, `case_id` null FK, `override_id` null (no FK; scoring owns overrides), `items` jsonb (`[{deed_id, effect, old_points?, new_points?}]`, ids and numbers only), `reason_key`, `created_at`, `appeal_until`, `final_at` null (set by the appeal decision or by `fraud.finalize_decisions` once `appeal_until` passes with no appeal). CHECK that exactly one of `case_id` and `override_id` is set, matching `kind`. `UNIQUE (case_id, user_id)` and `UNIQUE (override_id, user_id)`. Index `(final_at) WHERE final_at IS NULL`.

**`appeals`**: `id`, `notice_id` FK decision_notices UNIQUE (one appeal per notice), `user_id`, `reason` text (10–2000 chars), `created_at`, `reviewer_id` null FK users, `outcome` enum `upheld`, `overturned` null, `reviewer_note` null, `decided_at` null. CHECK that outcome, reviewer and decision time are all set or all null. The trigger allows deciding once. The service forbids the reviewer from being the admin who made the decision, or a conflicted admin (D90).

**`fraud_detector_runs`**: `id`, `detector` (`reciprocal`, `ring`), `trigger` (`sweep`, `cli`), `window_start`, `window_end`, `config_version`, `started_at`, `finished_at null`, `status` (`running`, `done`, `truncated`, `failed`), `edges_scanned`, `cases_opened`, `deeds_added`. Per-deed handler runs are not logged here (they are `processed_events` rows).

### 2.2 `scoring` additions (migration 0027)

**`score_overrides`** (append-only)

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | uuid PK | |
| deed_id | uuid FK deeds | |
| role | enum `doer`, `reporter` (existing `ledger_role`) | |
| points | int null | null = clear; CHECK `points IS NULL OR points >= 0` (upper bound from config in the service) |
| previous_points | int | active entry's points when written |
| reason_code | varchar(30) | from `fraud.override_reason_codes` |
| note | text | CHECK `length(btrim(note)) >= 5` |
| case_id | uuid null | no FK (scoring doesn't own cases); validated through `fraud.service` when given |
| actor_id | uuid FK users | |
| created_at | timestamptz | |

Index `(deed_id, role, created_at desc)`. Rows created from an approved request carry `request_id`.

**`override_requests`** (Q77): `id`, `deed_id`, `role`, `points`, `previous_points`, `reason_code`, `note`, `case_id` null, `requested_by` FK users, `created_at`, `expires_at`, `status` enum `pending`, `approved`, `declined`, `expired`, `decided_by` null FK users, `decision_note` null, `decided_at` null, `override_id` null (set on approval). CHECK `decided_by <> requested_by`. The trigger allows deciding once, from `pending` only. Partial unique `(deed_id, role) WHERE status = 'pending'`.

**`concentration_signals`**: `doer_id`, `reporter_id`, `period_month`, `share_bp int` (basis points at detection), `doer_points`, `detected_at`; PK `(doer_id, reporter_id, period_month)`. Insert-only. Makes `ReporterConcentrationExceeded` fire once.

**Enum and ledger changes:** `LedgerReason` gains `admin_override`. The `points_ledger_check_entry` trigger accepts `decision` rows with reason `admin_override`. Ledger rows written for an override carry `breakdown.override_id`.

No change to `hero_titles` / `user_badges`: their `revoked_*` columns exist (M4).

### 2.3 `deeds`

No schema change. `deeds_status` already has `rejected` and `verification_event_kind` has `rejected`. New code: `_reject.py`, `handlers.py` (subscriber for `FlagDecisionFinal`) and service reads (§3.1).

### 2.4 Config namespace `fraud` v1 (`fraud/_limits.py:FraudConfig`, no defaults; migration 0026)

```json
{
  "timezone": "Asia/Kolkata",
  "detectors_enabled": { "reciprocal": true, "ring": true, "concentration": true,
                         "invite_anomaly": false, "content": true },
  "hold_on_open": { "reciprocal": false, "ring": false, "concentration": false,
                    "invite_anomaly": false, "content": false },
  "explanation": { "reply_days": 14, "reply_max_chars": 2000 },
  "appeal": { "window_days": 30, "reason_min_chars": 10, "reason_max_chars": 2000 },
  "member_reason_labels": {
    "case": "These recognitions didn't meet our rule that recognitions should come from independent witnesses.",
    "fake_photo": "The photo didn't show this deed.",
    "rule_misfire": "Points corrected.",
    "fraud_confirmed": "These recognitions didn't meet our rule that recognitions should come from independent witnesses.",
    "other": "Points changed after review." },
  "reciprocal": { "window_days": 30, "min_deeds_each_way": 1 },
  "ring": { "window_days": 30, "min_size": 3, "max_size": 5, "min_edge_deeds": 1,
            "min_internal_share_pct": 50, "max_search_edges": 5000 },
  "concentration": { "max_reporter_share_pct": 30, "min_doer_points": 50 },
  "invite_anomaly": { "shared_device_accepts_per_pair_month": 3,
                      "new_account_hours": 24, "new_account_accepts_per_reporter_30d": 3,
                      "accept_within_minutes": 5 },
  "coi_lookback_days": 90,
  "note_min_chars": 10, "note_max_chars": 2000,
  "override_max_points": 50,
  "override_second_admin": { "max_cut_points": 10, "cut_to_zero": true, "expire_days": 14 },
  "override_reason_codes": ["fake_photo", "rule_misfire", "fraud_confirmed", "other"],
  "page_size": 50,
  "rate_admin_read": "600/60", "rate_admin_write": "60/60"
}
```

Validation on load: `min_size >= 3`, `max_size <= 6`, `min_size <= max_size`, percentages 1–100, windows 1–365 days, `reply_days` 3–60, `appeal.window_days` 7–90, a member label for every override reason code. Revision 2 keeps `hold_on_open` all `false` per PO principle 1; turning one on needs PO sign-off. `invite_anomaly` keys are typed now so the deferred detector doesn't need a new namespace; nothing reads them in M5 while `detectors_enabled.invite_anomaly` is false. The PRD §12 limits table gains rows for the reciprocal window, ring window/size/share, the concentration minimum and the COI lookback (DOC task).

---

## 3. API contract

### 3.1 Conventions and service additions

- Thin routers, `AppError` in services, opaque keyset cursors, `AdminUser` on every endpoint here (moderators 403, members 403, signed out 401). Conflicted admins get 404 (D90). Writes use `rate_admin_write`, reads `rate_admin_read`, per account.
- **`deeds.service` additions:**
  - `verified_edges(session, *, since, until, user_ids=None) -> list[Edge]` with `Edge(deed_id, reporter_id, doer_id, verified_at, counts_for_points)`: verified deeds only, ordered by `verified_at`. Index `deeds (verified_at) WHERE status = 'verified'` if missing.
  - `pair_signals(session, pairs) -> dict[(reporter, doer), PairSignals]` with `shared_device: bool`, `shared_network: bool`: computed inside `deeds` from `qr_scans.device_hash` / `ip_hash` (and `invites.shared_device`); the hashes never leave the module.
  - `related_users(session, user_id, since) -> set[uuid]`: users with a verified deed with `user_id` in either role since `since` (for the COI rule).
  - Re-export nothing new for events; `deeds/handlers.py` imports `FlagDecisionFinal` from `fraud.service`.
- **`fraud.service`** (for scoring, deeds, M8): `is_held(session, deed_id) -> bool`, `held_deed_ids(session, deed_ids) -> set`, `confirmed_case_for_user(session, case_id, user_id) -> bool`, `open_case_user_ids(session, user_ids) -> set` (M8 "no open flag cases"; `open` and `parked` count as open), `concentration_rule(session) -> ConcentrationRule`, events `DeedHoldChanged`, `FlagRaised`, `FlagCaseResolved`, `FlagDecisionFinal`, `AppealDecided`.
- **`scoring.service`** gains the event classes `ReporterConcentrationExceeded`, `DeedNeedsReview`, `HeroTitleRevoked`, `BadgeRevoked`.

### 3.2 Shapes

```
UserRef      { handle, display_name }                       # identity.service.public_summaries
CaseRow      { case_id, type, status, outcome|null, opened_at, updated_at, review_by|null,
               users: UserRef[] (first 5), user_count, deed_count, held_count, summary }
               # summary: server-made text, e.g. "Priya and Ahmed recognised each other 3 times in 12 days"
CaseDeed     { deed_id, reporter: UserRef, doer: UserRef, category_label, area_name,
               description, verified_at, status, counts_for_points, held: bool,
               outcome: "exclude"|"reject"|"release"|null }
CaseEdge     { from: UserRef, to: UserRef, deeds: int, shared_device: bool, shared_network: bool }
CaseUser     { user: UserRef, joined_at, verified_as_doer_in_window: int,
               verified_as_reporter_in_window: int, other_open_cases: int, earlier_confirmed_cases: int }
CaseDecision { action, note, actor: UserRef, created_at }
CaseDetail   { case: CaseRow + {signal, detector_config_version}, users: CaseUser[],
               deeds: CaseDeed[], edges: CaseEdge[], decisions: CaseDecision[],
               finalization: [{period_month, finalizes_at, final: bool}] }  # months of its deeds
```

`area_name` only, never coordinates (M3 D39). Points are not in the fraud response (`fraud` can't import `scoring`, D78); the UI fetches `GET /api/admin/scoring/deeds/{id}` on demand.

### 3.3 Fraud endpoints (`fraud` module, under `/api`)

| # | Method, path | Request | Response | Errors |
| - | ------------ | ------- | -------- | ------ |
| 1 | `GET /api/admin/fraud/options` | | `{types: [{id, label, holds_on_open}], statuses, outcomes, deed_outcomes, note_min_chars, note_max_chars, override_reason_codes: [{id, label}], override_max_points}` | |
| 2 | `GET /api/admin/fraud/cases?status=open\|parked\|resolved&type=&user={handle}&cursor=` | | `{items: CaseRow[], counts: {open, parked, resolved}, next_cursor}`; order: open/parked by `review_by` (nulls last), then `opened_at`; resolved by `resolved_at desc`. Conflicted cases are left out (counts too). `user=` filters by subject (used by revocation, F24) | 400 `invalid_cursor`, 404 unknown handle |
| 3 | `GET /api/admin/fraud/cases/{case_id}` | | `CaseDetail` | 404 `case_not_found` (also when conflicted) |
| 4 | `POST /api/admin/fraud/cases/{case_id}/park` | `{note}` | `CaseDetail` | 409 `case_not_open`; 422 `note_required`, `note_too_long`; 404 |
| 5 | `POST /api/admin/fraud/cases/{case_id}/unpark` | `{note}` | `CaseDetail` | 409 `case_not_parked`; 422; 404 |
| 6 | `POST /api/admin/fraud/cases/{case_id}/decision` | `{outcome: "confirmed"\|"dismissed", note, deeds?: [{deed_id, action: "exclude"\|"reject"\|"release"}]}` | `CaseDetail` | 409 `case_resolved`; 422 `note_required`, `deed_actions_invalid` (dismiss with `deeds`, or confirm with an unknown/missing deed: every case deed needs an action, the UI defaults to `exclude`); 404. A deed withdrawn meanwhile may still be marked `reject`: it is recorded and skipped by `deeds` |
| 7 | `GET /api/admin/fraud/status` | | `{config_version, detectors: [{detector, enabled, last_run: {status, started_at, finished_at, edges_scanned, cases_opened}|null}], open_cases_by_type}` | |
| 10 | `POST /api/admin/fraud/cases/{case_id}/explanations` | `{handles?: string[], note}` (default: every subject) | `CaseDetail` (gains `explanations: [{user, requested_at, reply_due_at, reply, replied_at}]`) | 409 `case_resolved`, `already_requested`; 422; 404 |
| 11 | `GET /api/admin/fraud/appeals?status=open\|decided&cursor=` | | `{items: [{appeal_id, user, kind, case_id\|null, deed_count, created_at, original_decider: UserRef}], counts, next_cursor}`. Leaves out appeals this admin may not review (decider or conflicted) | 400 `invalid_cursor` |
| 12 | `GET /api/admin/fraud/appeals/{id}` · `POST …/{id}/decision` | `{outcome: "upheld"\|"overturned", note}` | the appeal with its notice, items, the person's reason and, for a case, a link to the case | 404 (also for the decider and conflicted admins); 409 `appeal_decided`; 422 |
| 8 | `GET /api/admin/audit?entity_type=&entity_id=&actor={handle}&action=&from=&to=&cursor=` | | `{items: [{id, created_at, entity_type, entity_id, action, actor: UserRef\|null, reason, old_value, new_value}], next_cursor}` newest first, `page_size` | 400 `invalid_cursor`, `invalid_range` (from > to or > 366 days) |

**Decision effects (#6), one transaction, case row locked `FOR UPDATE`:**
- `dismissed`: nothing to release (open and parked cases never hold deeds, D80). No notice to anyone (they were never told, or they see "No action taken" on an explanation request they received).
- `confirmed`: first check D99 for every person with an adverse outcome on one of their deeds (409 `explanation_required` / `explanation_pending`). Then per deed: `exclude` → hold (created now if none); `release` → release (`released_on_confirm`); `reject` → hold now, rejected later when final (D100). Create a `decision_notices` row per person with an adverse item (`appeal_until = now + appeal.window_days`). A confirm where every deed is `release` is refused (422 `use_dismiss`).
- Write `flag_case_decisions`, `flag_case_deed_outcomes`, `audit_log` (`flag_case.confirmed` / `.dismissed`, `new_value` = counts per outcome, `reason` = note), set status/outcome, emit `FlagCaseResolved`.

**Appeal decision (#12):** `upheld` → set the notice's `final_at`; `overturned` → release that person's holds for the case (`appeal_overturned`), drop their deeds from pending rejection, emit `AppealDecided` (scoring clears an override), set `final_at`. Both write `audit_log` (`appeal.upheld` / `.overturned`). **`fraud.finalize_decisions`** (hourly): set `final_at` on notices past `appeal_until` with no appeal. Then, for each deed marked `reject` whose notices are now final for both people on it (and none overturned), emit `FlagDecisionFinal(case_id, rejected_deed_ids)`.

### 3.3b Member endpoints (`fraud` router, signed in, own data only)

| # | Method, path | Request | Response | Errors |
| - | ------------ | ------- | -------- | ------ |
| M1 | `GET /api/reviews` | | `{explanations: [{id, requested_at, reply_due_at, replied_at, reply, status: "waiting"\|"replied"\|"closed", outcome_text\|null, deeds: [MyDeedRef]}], notices: [{id, created_at, reason_text, items: [{deed: MyDeedRef, effect_text}], appeal_until, can_appeal, appeal: {created_at, reason, outcome\|null, decided_at\|null}\|null, final: bool}], unread: int}`. `MyDeedRef` = `{deed_id, role, category_label, area_name, occurred_on, other_person: UserRef}`, only the requester's own deeds | |
| M2 | `POST /api/reviews/explanations/{id}/reply` | `{reply}` | the item | 404 (not yours); 409 `reply_closed`, `already_replied`; 422 `reply_too_long` |
| M3 | `POST /api/reviews/notices/{id}/appeal` | `{reason}` | the notice | 404; 409 `appeal_closed`, `already_appealed`; 422 `reason_required`, `reason_too_long` |
| M4 | `POST /api/reviews/seen` | | `204` | |

`unread` drives the dot on the Profile tab. None of these responses names a case type, a detector, the case's other people or an admin.

### 3.4 Scoring endpoints (new; `scoring` module)

| # | Method, path | Request | Response | Errors |
| - | ------------ | ------- | -------- | ------ |
| 13 | `POST /api/admin/scoring/deeds/{deed_id}/overrides` | `{role: "doer"\|"reporter", points: int\|null, reason_code, note, case_id?}` | the M4 #9 body (`AdminDeedScoring`) plus `overrides: [{id, role, points, previous_points, reason_code, note, actor, case_id, created_at}]` | 404 (unknown deed, or conflicted: own deed or a related user, D90); 409 `nothing_to_override`, `override_unchanged`; 422 `note_required`, `invalid_reason_code`, `points_out_of_range`, `unknown_case` |
| 14 | `GET /api/admin/scoring/users/{handle}` | | `{user: UserRef, titles: [{id, label, period_month, region_name, level, place, revoked_at, revoked_reason}], badges: [{id, slug, label, awarded_at, revoked_at, revoked_reason}], doer_points, reporter_points}` | 404 (unknown, or the admin themself) |
| 15 | `POST /api/admin/scoring/titles/{title_id}/revoke` | `{case_id, note}` | the #14 body | 404 (unknown or own); 409 `already_revoked`, `case_not_confirmed` (not resolved `confirmed`, or the user is not a subject); 422 `note_required` |
| 16 | `POST /api/admin/scoring/badges/{badge_id}/revoke` | `{case_id, note}` | the #14 body | as #15 |
| 17 | `GET /api/admin/scoring/override-requests?status=pending\|decided&cursor=` | | `{items: [{id, deed_id, role, user: UserRef, previous_points, points, reason_code, note, requested_by: UserRef, created_at, expires_at, status}], counts, next_cursor}`. Leaves out the admin's own requests and conflicted ones | 400 `invalid_cursor` |
| 18 | `POST /api/admin/scoring/override-requests/{id}/decision` | `{decision: "approve"\|"decline", note}` | the request (plus the M4 #9 body on approve) | 404 (own request, conflicted, unknown); 409 `request_decided`, `request_expired`, `nothing_to_override` (the deed changed meanwhile); 422 `note_required` |

#13 returns **202** with the pending request (instead of 200) when the override needs a second admin. A second pending request for the same deed and role → 409 `request_pending`.

M4 #9 `GET /api/admin/scoring/deeds/{deed_id}` gains `held: bool` and `overrides[]`. M4 #3 `GET /api/scoring/me/ledger` omits prize-rank-only pairs (D95).

### 3.5 Who may do what

| Action | Member | Moderator | Admin | Conflicted admin |
| ------ | ------ | --------- | ----- | ---------------- |
| See that a case/hold exists | – | – | ✓ | – (404) |
| Park / unpark / hold / ask for explanation / confirm / dismiss | – | – | ✓ | – (404) |
| Reply to an explanation request, appeal a decision about own deeds | ✓ own | ✓ own | ✓ own | ✓ own |
| Decide an appeal | – | – | ✓ not the original decider | – (404) |
| Override points | – | – | ✓ (big cuts as a request) | – (404) |
| Approve or decline a big cut | – | – | ✓ not the requester | – (404) |
| Revoke title or badge | – | – | ✓ with a confirmed case | – (404) |
| Audit view | – | – | ✓ (own actions included) | ✓ |

---

## 4. Rules and processing

### 4.1 Lock order

- **Fraud open/join:** advisory `fraud_case:(type, subject_key)` → `flag_cases` row → `prize_holds` inserts. A case never takes deeds or scoring locks.
- **Decision:** `flag_cases` row `FOR UPDATE` → holds. Deeds rejection runs later in the deeds handler (`same_act` advisory → deed row), scoring later in its handler (its M4 day locks). No transaction holds locks of two modules.

### 4.2 Opening or joining a case (`fraud/_cases.py: raise_flag`)

1. Skip if `detectors_enabled[type]` is false.
2. Drop deeds already in a **dismissed** case with the same `subject_key` (D81).
3. Take the advisory lock; find the live case for `(type, subject_key)` or insert one (`open`), its `flag_case_users`, and emit `FlagRaised(case_id, type, user_ids, deed_ids)` only for a new case.
4. Insert `flag_case_deeds` (`ON CONFLICT DO NOTHING`). If `hold_on_open[type]` (all false; PO principle 1 and Q73): insert `prize_holds`; for each deed that went from not-held to held, emit `DeedHoldChanged(deed_id, held=true, case_id)`.
5. Update `deed_count`, `held_count`, `review_by`, `updated_at`.

### 4.3 Reciprocal and ring (`fraud/_detect.py`)

- **Per deed** (`DeedVerified` for R→D at `t`; skipped unless the deed is still `verified`): load `verified_edges(since=t − window, until=t)` for R and D (reciprocal) and the bounded neighbourhood (ring). Apply D83 and D84. Edges use server `verified_at`, never `occurred_at`.
- **Ring search:** BFS from D over out-edges in the window, depth ≤ `max_size − 1`, stopping on reaching R; load edges in batches per frontier through `verified_edges(user_ids=frontier)`; stop and record `truncated` after `max_search_edges`. Each found cycle is canonicalised (sorted members) so the same ring found from different deeds gives one `subject_key`.
- **Sweep:** for every edge in the window, run the same checks (reciprocal by pair grouping in SQL; rings by Johnson-style enumeration limited to `max_size`). Writes a `fraud_detector_runs` row.
- Withdrawn or rejected deeds drop out of the graph naturally; existing cases are not edited when that happens (the admin sees the deed status).

### 4.4 Concentration (`scoring/_concentration.py`)

`scoring.check_concentration` every 15 min, for open (not finalized) months, skipped when `detectors_enabled.concentration` is false:

```
per (doer, reporter): SUM(points) over doer rows WHERE counts_for_prize_rank AND period_month = m
per doer: total = SUM over reporters
signal if total >= min_doer_points AND reporter_sum * 100 > max_reporter_share_pct * total
```

Insert into `concentration_signals` (`ON CONFLICT DO NOTHING`); on insert, emit `ReporterConcentrationExceeded(doer_id, reporter_id, period_month, share_bp, deed_ids)` where `deed_ids` are that pair's prize-rank deeds in the month. Fraud opens a `concentration` case for {doer, reporter}. Deeds an admin has held leave prize rank, so they drop out of the measure.

### 4.5 Invite anomaly rules (deferred, D87; written down so the config is right)

When invites return, on `DeedVerified` with `method = invite_accept`, flag `invite_anomaly` for {reporter, doer} if any of: the pair has more than `shared_device_accepts_per_pair_month` shared-device accepts this month (below the hard 15, M2 D6); the accepting account was created less than `new_account_hours` before accepting and the reporter has at least `new_account_accepts_per_reporter_30d` such accepts in 30 days (sock puppets); the invite was accepted within `accept_within_minutes` of being created from the same network (`pair_signals.shared_network`). Hold on open. Human approval of an invite deed for prize rank will be an `approve_for_prize_rank` outcome emitted to `deeds` (it owns `prize_rank_eligible`).

### 4.6 Scoring changes (`scoring/_ledger.py`, `handlers.py`)

- `Reconciler.doer_target`: `counts_for_prize_rank = inputs.prize_rank_eligible and not await fraud.is_held(session, deed_id)`.
- Override: after computing an award target for a role, read the latest `score_overrides` row for (deed, role); if its `points` is not null, replace `points` with it (`0` → `decision` row, `deed_units = 0`, `counts_for_prize_rank = false`), set `breakdown.override_id`; change reason `admin_override`. The override endpoint itself calls `reconcile_deed` in the request transaction (under the M4 day locks), so the admin sees the result at once.
- New handler `rescore_held_deed` on `DeedHoldChanged` → `reconcile_deed`. `SCORING_EVENTS` in the architecture test gains `fraud.deed_hold_changed`.
- `DeedNeedsReview` is emitted in `_reconcile_locked` when a new rule-based evaluation has `human_review_required` and no earlier evaluation of the deed had it (so re-scores don't repeat it).
- `_finalize.pending_scoring_events` adds the fraud handler names (prefix `app.modules.fraud.`) to the cutoff barrier and a second check: `unprocessed_before([scoring hold handler], now, since=month start) == 0` (D93). Admin M4 #10 shows both counts.
- `featured_city_rows` for a finalized month filters `PointsLedger.created_at <= finalized_at` (D94).
- `_reads` owner ledger: hide reversal/award pairs with reason `prize_rank_changed` whose points are equal (D95).

### 4.7 Deeds rejection (`deeds/_reject.py`)

As D89. Under the `same_act` advisory lock and the deed row lock: skip unless `verified`; set `status = rejected`; update the group's `verified_count`, `counted_count` (if it counted) and `distinct_reporters`; if `distinct_reporters` drops below 2, run the M2 §3.4b invite prize-rank downgrade (no-op while invites are off); write `verification_events(kind='rejected', actor = case decider)` and `audit_log` (`deed.rejected`, `reason = "fraud_confirmed"`, `new_value = {case_id}`); emit `DeedRejected`. No promotion.

---

## 5. Events

**New, emitted by `fraud`** (ids, types and counts only):

| Event | `event_type` | Payload | Subscribers |
| ----- | ------------ | ------- | ----------- |
| `FlagRaised` | `fraud.flag_raised` | `case_id, type, user_ids, deed_ids` | none in M5 (M8 analytics; PRD §11) |
| `DeedHoldChanged` | `fraud.deed_hold_changed` | `deed_id, held, case_id` | `scoring` → `reconcile_deed` |
| `FlagCaseResolved` | `fraud.flag_case_resolved` | `case_id, type, outcome, user_ids, pending_reject_deed_ids, excluded_deed_ids, released_deed_ids, actor_id` | none in M5 (M6, M8) |
| `FlagDecisionFinal` | `fraud.flag_decision_final` | `case_id, rejected_deed_ids` | `deeds` → `reject_for_fraud` per deed |
| `AppealDecided` | `fraud.appeal_decided` | `appeal_id, notice_kind, case_id, override_id, user_id, outcome` | `scoring` → on `overturned` for an override, write a clearing override (`points = null`, reason `other`, note "Appeal overturned", actor = reviewer) |

**New, emitted by `scoring`:**

| Event | `event_type` | Payload | Subscribers |
| ----- | ------------ | ------- | ----------- |
| `ReporterConcentrationExceeded` | `scoring.reporter_concentration_exceeded` | `doer_id, reporter_id, period_month, share_bp, deed_ids` | `fraud` |
| `DeedNeedsReview` | `scoring.deed_needs_review` | `deed_id, doer_id, reason` | `fraud` |
| `PointsOverridden` | `scoring.points_overridden` | `override_id, deed_id, role, user_id, old_points, new_points, reason_code, actor_id` (only when points go down) | `fraud` → decision notice (D100) |
| `HeroTitleRevoked` | `scoring.hero_title_revoked` | `title_id, user_id, period_month, region_id, case_id` | none (M6) |
| `BadgeRevoked` | `scoring.badge_revoked` | `badge_id, user_id, badge_slug, case_id` | none (M6) |

**Consumed by `fraud`:** `DeedVerified` (reciprocal + ring), `ReporterConcentrationExceeded`, `DeedNeedsReview`, `PointsOverridden`. **Emitted by `deeds`:** existing `DeedRejected` with reason `fraud_confirmed` (consumed by `feed` and `scoring` as today).

**Jobs:** `fraud.sweep` (nightly 02:30 IST, queue `fraud`), `fraud.finalize_decisions` (hourly), `scoring.check_concentration` (every 15 min). Detector handlers run as `core.handle_event` jobs.

---

## 6. UI flows (frontend-developer)

### 6.1 Files and routes

`src/features/admin/`: `fraudApi.ts` (+ types), `FraudCasesPage.tsx`, `FraudCaseReviewPage.tsx`, `AppealsPage.tsx`, `AppealReviewPage.tsx`, `DeedScoringAdminPage.tsx`, `UserScoringAdminPage.tsx`, `AuditLogPage.tsx`. `src/features/reviews/`: `reviewsApi.ts`, `ReviewsPage.tsx` (route `/me/reviews`, signed in), the Profile tab dot; reuse `ReportQueue.tsx`, `reviewParts.tsx`, `AdminDialog.tsx`, `decisionError.ts`.
Routes behind `RequireRole(['admin'])`: `/admin/fraud`, `/admin/fraud/:caseId`, `/admin/scoring/deeds/:deedId`, `/admin/users/:handle`, `/admin/audit`. `AdminLayout` nav shows "Fraud cases", "Audit log" to admins only (moderators keep Photo and Comment reports). A moderator opening an admin-only route gets NotFound (M3 §3.4b #9).

### 6.2 Screens and states (copy _interim_, for PO review)

- **Fraud cases queue:** tabs "Open · {n}" / "Parked · {n}" / "Resolved · {n}", type filter chips (Reciprocal, Ring, Concentration, Content). Note: "A case is a pattern worth a look, not proof. Nothing changes for anyone until you decide. Decide before the month's boards become final if you can." Rows: type chip, server summary ("Priya and Ahmed recognised each other 3 times in 12 days"), up to 3 names + "+{n}", "{deed_count} deeds · {held_count} held", "Opened {age}", a deadline chip "Board final {date}" (red within 48 h), [Review]. Empty: "No open cases." Loading, error states.
- **Case review:** header (type, status, opened, deadline per month from `finalization`). "What was found": the summary and signal in words (ring: "Ravi → Kavya → Sneha → Ravi, 70 % of what they received came from each other"). People table (name, joined, deeds as doer/reporter in window, other open cases, earlier confirmed cases). Edges table (from → to, deeds, "Same phone used" / "Same network" chips). Deeds table: reporter → doer, category, area, verified date, status, "Held" chip, [Points] (expands the scoring admin data), link to the scoring admin page. Deeds also reported by someone outside the case get an "Independent witness too" chip. Decision history and an **Explanations** panel (asked, due date, reply). Actions: [Park], [Ask for an explanation] and [Dismiss], each with a note dialog. [Confirm] is disabled with "Ask the people involved for an explanation first" or "Waiting for a reply until {date}" until D99 is met. It opens a dialog with a per-deed choice: Release (the default), Keep but exclude from leaderboards, or Reject the deed. The dialog also needs a note and explains: "People are told the outcome for their own deeds and can ask for another review within 30 days. A rejection only happens after that." Resolved cases are read-only and link to "Revoke titles or badges" for each subject (→ user page).
- **Deed scoring (admin):** M4 #9 ledger table, "Held for review" banner when held, overrides history, form: role (Doer / Reporter), new points (or "Back to the rules"), reason (select), note (required, counter), optional case. Errors from `nothing_to_override` ("This report earns no points to change: it is capped, not counted or no longer verified."). Banner when the month is final: "This month is final: the change shows in the person's points but not on the final leaderboard."
- **User scoring (admin):** titles and badges with [Revoke] each; the dialog lists the user's confirmed cases (`GET …/cases?user=&status=resolved`, filtered to confirmed) to pick from, plus a note. No confirmed case → the button is disabled with "Revoking needs a confirmed fraud case about this person."
- **Audit log:** filters (entity type, entity id, actor handle, action, date range), table newest first, JSON old/new values in an expandable row, "Load more".
- **Appeals (admin):** queue "Open · {n}" / "Decided"; the review page shows the person's reason, their notice items and a link to the case, with [Uphold] and [Overturn] buttons, a note dialog and "This answer is final."
- **Reviews (member, `/me/reviews`; dot on the Profile tab while `unread > 0`):**
  - **Explanation request:** "We're taking a closer look at some of your recent deeds. Most of the time everything is fine. If you'd like, tell us about them." Lists their deeds, then a text box, "Reply by {date}" and [Send]. Afterwards: "Thanks, we've got your reply." Once closed with no action: "We've finished looking. No changes."
  - **Decision notice:** the reason text, then each deed with its effect ("Not counted for leaderboards", "Will be removed on {date} unless you ask for another review", "Points changed from 12 to 8"). Then [Ask for another review] (until {date}), a reason box, and the result once decided ("We looked again and restored your deeds" / "We looked again and the decision stays").
  - Copy follows D101, with no "fraud" and no other people's names.
- My points stops listing hold/release pairs (backend). A rejected deed shows "Removed after review" in My deeds, linked to its notice.

### 6.3 Frontend rules

All labels, thresholds and reason lists from `/api/admin/fraud/options`; no client-side rules or dates computed. Query keys `['admin','fraud', status, type, user]`, `['admin','fraud','case', id]`, `['admin','audit', filters]`.

---

## 7. Task list

Agents: **DB**, **BE**, **FE**, **QA**, **SEC**, **AD**, **DOC** (main session). Starts after the PO approves revision 2.

| ID | Owner | Task | Depends on | Parallel |
| -- | ----- | ---- | ---------- | -------- |
| DB10 | DB | Migration 0026: `fraud` tables (§2.1) with triggers, CHECKs, partial uniques, indexes; seed `fraud` v1 config | – | with B38 |
| DB11 | DB | Migration 0027: `score_overrides`, `concentration_signals`, `LedgerReason.admin_override`, ledger trigger accepts `admin_override` decision rows; index on `deeds(verified_at) WHERE status='verified'` if missing | – | with DB10 |
| B38 | BE | `fraud` skeleton: registry entry, `_limits.FraudConfig`, `events.py`, `service.py` reads (§3.1); architecture tests for D78 allow-lists (fraud imports; scoring's fraud imports; deeds' fraud import; no feed/media), `SCORING_EVENTS` update, a test that proves the new rules bite | DB10 | with B39 |
| B39 | BE | `deeds.service`: `verified_edges`, `pair_signals`, `related_users`; `deeds/_reject.py` + `deeds/handlers.py` for `FlagDecisionFinal` (D89) | – | with B38 |
| B40 | BE | `fraud/_cases.py` (raise/join, holds, events), `_detect.py` reciprocal + ring, `handlers.py`, `jobs.py` sweep, `cli.py` | B38, B39 | |
| B41 | BE | `fraud/_review.py` + router #1–#7: queue, detail, park/unpark/decision, COI (D90), audit writes, rate limits | B40 | with B42 |
| B42 | BE | Scoring: hold in `reconcile_deed`, `DeedHoldChanged` handler, `_concentration.py` + job + event, `DeedNeedsReview`, finalization barrier (D93), Featured City freeze (D94), owner-ledger filter (D95) | B38, DB11 | with B41 |
| B43 | BE | Scoring: overrides in `reconcile_deed` + endpoint #13, #14, revocations #15–#16 with events, COI; override requests for big cuts (#17–#18, expiry in `fraud.finalize_decisions`' sibling job `scoring.expire_override_requests`) | B42 | |
| B44 | BE | Audit view #8 | B38 | any time |
| B45 | BE | Explanations, notices and appeals (D99–D101): tables in DB10, endpoints #9–#12 and M1–M4, `fraud.finalize_decisions`, `PointsOverridden` / `AppealDecided` handlers, D99 checks on confirm | B41, B43 | |
| F20 | FE | `fraudApi.ts`, types, admin nav and routes (admin-only) | B41/B43 contract | with B41 |
| F21 | FE | Fraud queue + case review pages | F20 | |
| F22 | FE | Deed scoring admin page with override form (shows "Needs a second admin" for big cuts); override requests queue and decision dialog | F20 | with F21 |
| F23 | FE | User scoring admin page with revoke dialogs | F20 | with F21 |
| F24 | FE | Audit log page | F20 | with F21 |
| F25 | FE | Member Reviews page, Profile tab dot, "Removed after review" link; admin appeals queue and review page; explanation panel and Confirm gating on the case page | B45 contract | with F21 |
| QA10 | QA | Backend tests per §8 | B40–B44 | |
| QA11 | QA | Frontend tests per §8 | F21–F24 | |
| SEC5 | SEC | Review per §9 | QA10, QA11 | |
| AD4 | AD | Diagrams: fraud module, event hops (verify → detect → hold → scoring), finalization barrier | SEC5 | |
| DOC5 | DOC | CLAUDE.md (fraud module entry, append-only list: `flag_case_decisions`, `flag_case_deed_outcomes`, `score_overrides`), ROADMAP M5 + invites precondition (D87), PRD §12 limits rows and item 9 resolved, §6.5 wording per Q68 | AD4 | |

---

## 8. Test plan (qa-engineer)

### Detectors

- **Principle 1:** opening any case type changes nothing: no hold, same ledger, same board, same feed, nothing in `GET /api/reviews`.
- Reciprocal: A→B then B→A 29 days later → case {A,B} with both deeds, nothing held; 31 days → no case; `min_deeds_each_way = 2` → needs 2 each way; withdrawn deed doesn't count; window from config (publish v2 with 7 days).
- Ring: A→B→C→A in 30 days with internal share ≥ 50 % → one case (same `subject_key` whichever edge triggers it); share 49 % → none; size 6 with `max_size 5` → none; 2-cycles go to reciprocal only; `max_search_edges` hit → run `truncated`, no case.
- Idempotency: same `DeedVerified` handled twice, sweep after handler, sweep twice → same cases, deeds and holds; one `DeedHoldChanged` per real change.
- Join: a new A→B deed while the reciprocal case is open (or parked) joins it and is not held; after dismissal the old deeds are never re-flagged, a new deed opens a new case.
- Concentration: doer with 60 prize-rank points, 20 from Priya (33 %) → one signal and one case, no hold; doer with 40 points → none (below minimum); re-running the job → no second event; finalized month not scanned.
- Content: a `spam_or_empty` deed opens one content case, no hold; re-score doesn't open a second.
- Detectors disabled in config → nothing opens; the sweep honours it.

### Holds, scoring and finalization

- Admin hold → doer row reversal + award with `counts_for_prize_rank=false`, same points; next board refresh drops the deed; Featured City drops it (open month); profile Verified Deeds and badges unchanged.
- Release on dismissal → back on the board; a deed in two cases stays held until both release.
- Reporter rows unaffected. Invite deed (`invites_on`): not eligible + held → stays out; eligible + released → back.
- Owner ledger shows no hold/release pair; `PointsChanged` for it has `delta=0`.
- Barrier: a `DeedVerified` before the cutoff not yet processed by fraud blocks finalization; a `DeedHoldChanged` written after the cutoff but not yet handled by scoring blocks it; once handled, the final board excludes the held deed.
- After final: a new hold, a dismissal, an override and a rejection write ledger rows in the final month; final snapshots, titles and Featured City (final) unchanged.

### Review

- Park/unpark/confirm/dismiss transitions and 409s; note required (min chars from config); every action writes `flag_case_decisions` and `audit_log`; decisions append-only (UPDATE/DELETE rejected).
- Confirm with `exclude` creates or keeps the hold; `release` releases; `reject` holds now and does **not** reject until final. Once final → deed `rejected`, feed post gone, doer and reporter points reversed, group counters updated, **no promotion**, `verification_events` row; a deed withdrawn meanwhile is skipped.
- Explanation (D99): confirm with an adverse outcome and no request → 409 `explanation_required`; requested and not due, no reply → 409 `explanation_pending`; reply sent or due date passed → allowed; dismiss always allowed; all-release confirm → 422 `use_dismiss`. Reply after the due date → 409; second reply → 409; another member's request → 404.
- Notices and appeals (D100): confirm creates one notice per person with an adverse item; a lowering override creates one, raising doesn't. Appeal within the window → open; after → 409; second → 409. The deciding admin and conflicted admins don't see it (404). Overturn → holds released, pending rejection cancelled (deed stays verified after the window), override cleared, title revocation 409. Uphold → final; the rejection runs on the next `finalize_decisions`. No appeal → final after the window; a deed is rejected only when the notices of both people on it are final.
- Member copy: no response under `/api/reviews` contains "fraud", the case type, a detector name, other subjects or the admin (schema and text scan).
- COI: subject admin, admin who reported/did a case deed, admin with a verified deed with a subject 89 days ago → case missing from the list and 404 everywhere; 91 days ago → allowed. Moderator → 403 on every endpoint.

### Overrides and revocation

- Big cut (Q77): 12 → 0 or 25 → 12 → 202 pending request, ledger unchanged, no notice; a second admin approves → applied, audited, notice sent; declines → nothing changes; the requester can't decide their own (404); expiry → `expired`; 12 → 5 (cut 7) applies at once.
- Override doer 12 → 10 (`fake_photo`): reversal + award reason `admin_override`; a later re-score keeps 10; clear (`points: null`) → back to rules; override to 0 → decision row, deed no longer counted on boards; capped/withdrawn deed → 409; out of range → 422; own deed → 404; audit row with old/new points.
- Revoke title before the user's decision is final (appeal window open or appeal pending) → 409 `decision_not_final`.
- Revoke title with a confirmed case whose decision is final for the user → revoked, gone from profile and heroes lists, final board unchanged, nobody else gets it; dismissed or open case → 409 `case_not_confirmed`; twice → 409.

### API and privacy

- No public or member endpoint returns anything about cases or holds (schema scan of every non-admin response model for `held`, `case`, `flag`).
- Case detail and events contain no coordinates, phone numbers, device or IP hashes, descriptions in events.
- Audit view: admin-only, filters, cursor, range limit.

### Frontend

- Queue tabs, filters, deadline chip; review page actions and dialogs with per-deed choices; 404 → NotFound; override and revoke forms and error copy; admin nav hidden for moderators.

---

## 9. Security and privacy concerns (security-reviewer)

- **Integrity:** no client input reaches `counts_for_prize_rank`, holds, deed status or ledger rows except through admin endpoints that write append-only decisions and audit rows. Overrides go through `reconcile_deed` (one ledger writer).
- **Admin abuse:** overrides and revocations are powerful; check required notes, reason codes, audit rows with old/new values, the COI rule (including the lookback) and 404 for conflicted admins. Consider (PO) a second-admin approval for overrides above a threshold; not in this draft.
- **Two-step login for admins** is already a before-launch item (M3 Q34); M5 raises its importance.
- **Privacy of flags:** cases never reach the public. Members hear about one only through an explanation request or a decision notice, which shows only their own deeds (D101). The owner ledger filter applies. A flagged admin can't see their own case or decide an appeal about it.
- **Tipping off (accepted trade-off):** asking for an explanation tells a colluder that their deeds are being looked at. The PO chose fairness (principle 3). The neutral text names no detector, rule or other person, and admins choose when to ask.
- **Appeal abuse:** one appeal per notice, a written reason, a different admin, and a final answer. Rate limits apply to M2–M3.
- **Data minimisation:** device/IP overlap only as booleans from `deeds.service`; hashes never leave `deeds`; no coordinates anywhere in fraud.
- **Finalization correctness:** the extended barrier; a stuck fraud event blocks finalization rather than finalizing wrong (visible in M4 admin #10).
- **Detector cost and DoS:** bounded ring search (`max_search_edges`), window limits, the M2 monthly report limits bound graph growth; sweep runs nightly on its own queue.
- **Gaming the detectors:** reciprocal pairs can spread reports over more than 30 days or route through a third person; rings larger than `max_size` escape; config can tighten both. Withdraw-and-repeat (M2 note) and edit frequency are not detected in M5 (noted for later).
- **False positives:** families and close friends will trigger reciprocal cases. Detection has no effect (D98). Admin holds can be reversed, and anything adverse comes after an explanation and can be appealed.
- **Two admins needed for appeals:** an appeal needs an admin who did not make the decision. With one admin, appeals wait, permanent steps stay paused, and the admin status page shows "Appeals need a second admin" (PO Q78). Big-cut override requests also need a second admin (Q77).
- **Audit view exposure:** check that no `audit_log` `old_value`/`new_value` written so far holds secrets, phone numbers or coordinates before exposing it in the UI.

---

## 10. Before-launch list (additions for ROADMAP)

- Invites may not be switched on until the invite-anomaly detector, invite human review and SEC3 L7 ship (D87).
- PO approval of the admin copy and the member-facing "Removed after review" wording.
- Review staffing: at least **two** admins (appeals need a second one), and someone looking at the queue before each month's cutoff (2nd of the month, about 01:00 IST).
- M6: push/email notices for explanation requests and decisions (in-app only in M5).
- M8: apply the 30 % concentration cap in prize ranking. An open case delays a payout until it is decided and never denies it (`fraud.service.open_case_user_ids`).

---

## 11. Questions for the PO, and answers

**PO answers (2026-11-26):** the PO answered with three principles (§0.0). Where a recommendation fits them, it stands; four were changed to match them.

| # | Answer | Where |
| - | ------ | ----- |
| Q59 | B: 30 days rolling, 1 deed each way (config) | D83 |
| Q60 | **Changed → B.** Detection holds nothing. Only an admin's confirmed decision takes a deed off the board. | D80, D98 |
| Q61 | A: case only, from 50 points; hard cap in M8 | D85 |
| Q62 | A: 3–5 people, 30 days, ≥ 50 % mutual | D84 |
| Q63 | A: not built; precondition for invites | D87 |
| Q64 | **Changed.** Nothing while unreviewed. Before any adverse decision the person is asked for an explanation, then told the outcome for their own deeds in neutral words. | D95, D99, D101 |
| Q65 | **Changed → in-app appeal.** One appeal per decision, decided by a different admin. Rejection and revocation wait for it. | D100 |
| Q66 | A: per deed, with **Release as the default** (changed from Exclude) | D88 |
| Q67 | A: board frozen, title not passed down. **Revocation only once the decision is final** (after appeal). | D92, D100 |
| Q68 | A: open cases never hide a deed | A13 |
| Q69 | A: admins only, 90-day conflict rule; appeals go to a different admin | D90, D100 |
| Q70 | A: 0–50, any time, audited; **lowering is told to the person and can be appealed** | D91, D100 |
| Q71 | A: content case, no hold | D86 |
| Q72 | B: "Unavailable account" once the revocation is final | D92 |
| Q73 | **C. Never.** Admins can't hold deeds before deciding; only a confirmed decision takes a deed off the board | D80, D98 |
| Q74 | A. 14 days to reply to an explanation request (`explanation.reply_days`) | D99 |
| Q75 | A. 30 days to appeal (`appeal.window_days`) | D100 |
| Q76 | A. In M8, an open case delays a cash payout until decided, with a time limit; it never denies it | A14, §10 |
| Q77 | **B.** A second admin must approve an override to 0 or a cut of more than 10 points; smaller changes apply at once | D91, #17–#18 |
| Q78 | A. With no other eligible admin, an appeal waits and permanent steps stay paused; the status page warns | D100, §9 |

The original questions follow for the record. Q73–Q78 were asked on 2026-11-26 in the multiple-choice format the PO now prefers.

**Q59. How short is the "short period" for reciprocal verification?**
Example: Priya recognises Ahmed on 1 November; Ahmed recognises Priya on 20 November.
Options: A. 7 days. B. 30 days, rolling (any two dates up to 30 days apart). C. The same IST calendar month.
Recommendation: **B**, with one deed each way enough to open a case. 7 days misses slow, deliberate swaps; calendar months miss 31 Oct / 1 Nov. Both numbers are config.

**Q60. Is a flagged deed taken off the leaderboard immediately, or only after review?**
Example: The detector flags Priya and Ahmed on 28 November; an admin looks at it on 3 December, after the board became final.
Options: A. Immediately for reciprocal and ring cases (PRD item 9), back on the board if dismissed before finalization. B. Only after an admin confirms fraud. C. Immediately, but only for people currently in a titled place.
Recommendation: **A.** It is what the PRD says, and it is invisible to members except as a rank change. B lets colluders keep titles whenever review is slow.

**Q61. What does the 30 % reporter concentration rule do?**
Example: In November Ahmed has 60 leaderboard points; 24 of them (40 %) come from deeds his sister Fatima reported.
Options: A. In M5 it only opens a case for review (nothing held, boards unchanged) once a doer has at least 50 prize-rank points; the hard cap ("only 30 % counts") is applied to cash-prize ranking in M8. B. Cap it on every leaderboard now: points above 30 % from one reporter don't count. C. Open a case and hold all of that reporter's deeds for the doer until reviewed.
Recommendation: **A.** B would change approved boards for most new users (with 1–3 reporters anyone is over 30 %) and clashes with the "3 different reporters" prize rule (three equal reporters are 33 % each). The M8 cap also needs a PO decision then: count up to 30 % per reporter, with a minimum of 4 distinct reporters?

**Q62. What counts as a ring?**
Example: Ravi recognises Kavya, Kavya recognises Sneha and Sneha recognises Ravi, all in November; 7 of the 10 deeds they received came from each other.
Options: A. A cycle of 3 to 5 people in the last 30 days, where at least 50 % of what the members received came from each other. B. Any cycle of 3 to 5 people in 30 days. C. Only cycles of exactly 3.
Recommendation: **A.** B flags ordinary friend groups who also get recognised by others; C misses 4- and 5-person rings. All numbers are config.

**Q63. Invite anomalies, while invites are switched off?**
Options: A. Don't build the detector in M5; make it, the human review of invite deeds and the SEC3 L7 fix a precondition for switching invites back on. B. Build it now, switched off, tested with invites on. C. Drop it.
Recommendation: **A.** No invite deed can occur today, so B is code nobody uses, built against rules that may change when invites return.

**Q64. What do flagged people see?**
Example: Priya and Ahmed are in an open reciprocal case; later an admin rejects one of the deeds.
Options: A. Nothing while a case is open (no notice, no label; only their leaderboard rank may drop). After confirmation, a rejected deed shows "Removed after review" in My deeds, with no mention of fraud, other people or detectors. B. Tell both people when a case opens ("Some of your recent deeds are being reviewed"). C. Nothing ever, not even for rejected deeds.
Recommendation: **A.** Telling people while a case is open teaches colluders what triggers it and upsets honest friends; saying nothing after a rejection would look like a bug.

**Q65. Can people appeal?**
Options: A. No in-app appeal in M5: a support contact on the help page, and an admin who agrees can reverse the effects (override points back; a rejected deed can't be restored). B. An "Ask for a review" button on a rejected deed that opens a new case for a different admin. C. No appeal.
Recommendation: **A** for launch, revisited with real volumes; B adds a member-facing flow and a second review path.

**Q66. What happens to deeds when fraud is confirmed?**
Example: In a confirmed ring of Ravi, Kavya and Sneha, two deeds were staged and one was a real rescue another witness also reported.
Options: A. The admin chooses per deed: keep it but exclude it from leaderboards for good, reject it (it leaves the feed and its points are reversed for doer and reporter), or release it. B. Reject every deed in the case. C. Only exclude, never reject.
Recommendation: **A**, with "exclude" as the default. A rejected deed does **not** promote the next report of the same act.

**Q67. What happens after a month is final?**
Example: November finalizes on 2 December with Ahmed as Koramangala Kindness Hero; on 10 December a ring including Ahmed is confirmed.
Options: A. The final board stays as it is; the admin may revoke Ahmed's title (and badges) citing the confirmed case; nobody else receives it. A case dismissed after finalization doesn't restore a title the person missed. B. Re-finalize: recompute the board and pass the title down. C. At finalization, titles of people with an open case wait as "pending" until the case is decided.
Recommendation: **A.** It keeps "final means final" (D64, your QA2 answer), and the queue deadline pushes reviews before the cutoff. B reopens months; C adds a title state and delays.

**Q68. The PRD says flagged deeds disappear from the feed. Should an open flag hide a deed?**
Options: A. No: only deeds rejected after review leave the feed. B. Yes: hide while any case about it is open. C. Hide only for ring cases.
Recommendation: **A.** Hiding on an automatic flag is a public accusation before anyone has looked; most reciprocal flags will be friends doing real good. PRD §6.5 wording to change to "rejected (after review)".

**Q69. Who reviews fraud cases and who may override points?**
Options: A. Admins only, for cases, overrides, revocations and the audit log; moderators keep photo and comment reports. Conflict of interest: an admin who is in the case, did or reported one of its deeds, or recognised (or was recognised by) one of its people in the last 90 days can't see or decide it. B. Moderators can see and park cases; only admins confirm, dismiss and override. C. Moderators and admins equally.
Recommendation: **A** (your Q34 rule: later admin areas are admin-only). The 90-day lookback is config.

**Q70. How far can a score override go?**
Options: A. Any verified deed, doer or reporter points, from 0 up to 50, any time (after a month is final the person's points change but the final board doesn't), with a reason code and a written note, always audited. B. Only while the month is still open. C. Doer points only.
Recommendation: **A.** Fake-photo bonuses (M4 A3) are often found late; the final board is protected anyway.

**Q71. Should "spam or empty" reports open a review case?**
Example: Arjun reports Rahul with the description "good good good"; the rules give Rahul 4 points instead of 10.
Options: A. Yes, a `content` case without any hold; the admin dismisses it or overrides the points. B. No case; just leave the minimum points (M7 moderation handles content later). C. A case that holds the deed out of the leaderboard.
Recommendation: **A**, switchable off in config if the queue gets noisy.

**Q72. How does a final leaderboard show someone whose title was revoked for fraud?**
Example: Ahmed's November Koramangala Hero title is revoked on 10 December.
Options: A. The row stays as frozen (rank and name), without the title marker. B. The row shows "Unavailable account" (as for suspended accounts, SEC3 L6), place unchanged, no title. C. The row is removed and the places below keep their numbers (a gap at #1).
Recommendation: **B.** It doesn't keep celebrating a confirmed cheat by name, doesn't reshuffle a final month, and reuses built display code.
