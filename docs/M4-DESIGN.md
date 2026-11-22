# M4 Scoring, Points & Leaderboard: design

**Status:** Revision 2 (2026-11-22). All PO questions Q45–Q58 are answered (§11). Q52 was replaced by a 24-hour withdrawal window with monthly finalization, and Q54 by a Kindness Hero for each level of a location hierarchy. Q58 = dense ranking (1, 1, 2). Revision 1 (2026-11-21) was the draft for PO review.
**Baseline:** M3 as approved (`m3-feed`, commit b67120a; migrations up to 0018). Branch `m4-scoring`.
**Sources used:** PRD §4, §5 (anti-collusion, monthly limits, edit/withdraw, profile counts), §6.4, §6.5 (visibility never affects points; photos, Q29, Q30), §6.9, §6.10, §8, §9, §10, §12; ROADMAP M4; `docs/M2-DESIGN.md` (D2, D4, D13, D15, §3.4b, §4 events); `docs/M3-DESIGN.md` (D30, D36, D37, D39, Q28); the code under `backend/app` (`modules/deeds`, `media`, `feed`, `core/runtime_config.py`, `core/events.py`, `tests/test_architecture.py`) and `frontend/src/features/profile/PublicProfilePage.tsx`.

Decision numbers continue from M3 (D46 onwards) and question numbers from Q45, so no number means two things.

---

## 0. Decisions

### 0.1 Architecture decisions

| #   | Decision | Why | Where |
| --- | -------- | --- | ----- |
| D46 | **One new module, `scoring`** (`backend/app/modules/scoring/`). It owns rule-based evaluation, the evidence record, the points ledger, leaderboards, badges, Kindness Hero titles, the public tier and (slice M4b) the AI shadow evaluator and the AI shadow tie-break. | They share one config family and one source of truth (the ledger). The name `scoring` is already covered by the D30 architecture test (`SCORING_NAMES`). | §2, §3 |
| D47 | **Imports:** `scoring` may import `app.core`, `deeds.service` and `identity.service` only. **Never `feed`** (existing D30 test) and **never `media`** (new test). Photo evidence reaches scoring only through the new `media` event `DeedPhotoReady` (§5). | Visibility (feed hides, PO Q51) and photo state (media, Q29) can never influence points, leaderboards or titles, by construction. | §5, B34 |
| D48 | **The points ledger `points_ledger` is append-only** (`forbid_modification()` trigger). A change is a **reversal row** (points negated, `reverses_entry_id` set and unique) plus a new award row. Zero-point **decision rows** record capped outcomes. | CLAUDE.md, PRD §10.7. Decision rows make caps explainable and stable (D52). | §2.3 |
| D49 | **One idempotent `reconcile_deed(deed_id)`** computes the target doer and reporter entries from the deed's current state (read live through `deeds.service`), the frozen evidence and the frozen award inputs, and writes reversal + award only when they differ. Every handler calls it. | Same pattern as M3 D37 `sync_post`: handlers can run late, twice or out of order. | §4.2 |
| D50 | **Award-time freeze.** On a deed's first doer decision, the inputs that depend on *other* deeds are frozen in the entry's `breakdown`: the daily-cap outcome, the pair ordinal and the multiplier, and the deed's `region_id` (D66). Re-scores inside the withdrawal window reuse them. | One withdrawal must not cascade re-scores across a doer's month. | §4.2 |
| D51 | **Photo evidence is a high-water mark (Q29).** `scoring_evidence.first_photo_ready_at` is set once by the `DeedPhotoReady` handler for a photo attached within the 24 h add window, and a trigger forbids changing or clearing it. The photo bonus and the AI evidence level read only this record. No scoring handler exists for any photo removal. | A QR deed is verified before photos are attached (M3 D31), so "freeze at verification" would never give it the bonus. "Ever had a processed photo in the add window" can only grow, so a removal by the doer, the uploader or an admin can't change points. | §2.3, §4.3 |
| D52 | **Daily cap (10 per doer per IST day of verification) counts doer decisions, not standing deeds.** The 11th deed gets a zero-point `doer_daily_cap` decision. A withdrawal of one of the first 10 doesn't promote the 11th. | PRD §5. The verification day is set on our server (Asia/Kolkata), never from the client's `occurred_at`. With no promotion the cap stays stable (D50) and matches the pair-day rule. | §4.2 |
| D53 | **Points are whole numbers.** A multiplier result is rounded half-up, and any award is at least 1 point. | Profiles that add up on screen. | §2.3 |
| D54 | **Rule-based scoring is per counted report**, from that report's own category and evidence (PO Q46 = A). Every run writes an `impact_evaluations` row (`method='rule_based'`, `mode='live'`). | Reports in one act can carry different categories (M2 D2). | §2.3 |
| D55 | **Text rules:** keyword lists and a few fixed patterns from config, matched on the normalised description (≤ 1000 chars). `spam_or_empty` gives `min_points` and sets `needs_review` (read by an M5 queue). `category_mismatch` is recorded only. Patterns are validated when the config loads and run with `re` on bounded input. | PRD §9: "every verified deed gets a minimum score". English keyword lists are crude, so a mismatch shouldn't cost the doer points. | §4.4 |
| D56 | **Edits re-score only inside the withdrawal window.** A `DeedEdited` with `category` or `description` changed, made (by the revision's time) within `withdraw_window_hours` of verification, re-runs the rules and reconciles. After the window, edits still update the text but never change points (PO, Q52 replacement). | Points, badges and boards settle 24 h after verification. | §4.2 |
| D57 | **Reporter points never count for prize rank or any leaderboard**, enforced by a CHECK on the ledger: `role <> 'reporter' OR NOT counts_for_prize_rank`. Doer rows take `counts_for_prize_rank` from `deeds.prize_rank_eligible`. | PRD §9. | §2.3 |
| D58 | **The ledger month is the IST month of verification** (`earned_on`, `period_month`, from server time). A reversal copies them from the row it reverses. | PO (Q52 replacement): the client's `occurred_at` is never used. | §2.3 |
| D59 | **Leaderboards are periodic snapshots, one per (region, month).** `scoring.refresh_leaderboards` (every 5 min, config) aggregates doer prize-rank ledger rows for the open month(s) per region and level. Rank score = `w_deeds × deed_count + w_points × doer_points` (weights in config, default 1 and 1). Snapshots stay **provisional** until finalized (D64). | Cheap, stable reads; 369 wards + 1 city come from one grouped query over the region closure. | §2.3, §4.5 |
| D60 | **All tunables are config rows** in three new namespaces typed without defaults: `scoring` (`ScoringConfig`), `leaderboard` (`LeaderboardConfig`: weights, levels, titles, minimums, tiers, badges, finalization) and `ai_eval` (`AiEvalConfig`). `deeds` gains v7 (`withdraw_window_hours`, `fallback_region_code`). `app/core/runtime_config.py` gains `load_version(session, model, version)`. | CLAUDE.md. | §2.6 |
| D61 | **The AI shadow evaluator is slice M4b**, built after the PO reviews M4a, and **ships switched off** (`ai_eval.enabled=false`, no API key). It also hosts the AI shadow tie-break (D68). PO Q57 = A. | It is independent, sends user text to an outside processor, and its value comes from real launch data (PRD §12.3). | §4.7 |
| D62 | **Shadow AI can't affect points or ranks**, enforced three ways: (1) DB CHECK on `impact_evaluations`: `method <> 'ai' OR mode = 'shadow'`; (2) `_ledger.py`, `_rules.py`, `_leaderboard.py` never import `_ai*.py` (architecture test) and never read `method='ai'` rows; (3) `ai_eval.mode` is typed `Literal["shadow"]`. | ROADMAP M4, PRD §12.3. | §2.3, B34 |
| D63 | **AI evaluator details (M4b):** one Procrastinate job per counted verified deed (queue `ai`), the Anthropic Python SDK, the model id from `ai_eval.model` (seed `claude-opus-5-5`; `claude-sonnet-5-5` or `claude-haiku-5-5` is a config change), structured output, `effort` from config. The model returns six 0–100 sub-scores, a rationale, estimated beneficiaries and a confidence. **Our code** computes the weighted sum and applies the evidence cap. Idempotency key `(deed_id, input_hash)`. | Weights and caps stay auditable and versioned in our config, not in a prompt. | §4.7 |
| D64 | **24-hour withdrawal window and monthly finalization** (PO, Q52 replacement, 2026-11-22). A reporter can withdraw a verified deed only within `deeds.withdraw_window_hours` (24) of `verified_at`; a pending invite can still be cancelled at any time. Points are shown as *provisional* to their owner until the window ends. `scoring.finalize_month` finalizes a month only after (a) month end + window + `finalize_margin_minutes` (60), so November finalizes at about 2 Dec 01:00 IST, **and** (b) every outbox event created before that cutoff has been processed by every scoring handler. It runs under a per-month advisory lock and is idempotent. Final snapshots and titles are frozen; only confirmed fraud, through admins in M5, changes them. | Gives a fixed point after which nothing in a month can move, so titles are awarded once and never reshuffled. Condition (b) closes the outbox delay. No data migration: there are no real deeds yet. | §4.5, §4.6 |
| D65 | **Location hierarchy as data:** a `regions` table (`kind` country / state / city / ward, `parent_id`, `name`, `code`) plus a `region_closure` table (ancestor, descendant, depth), owned by `deeds` (which owns locations and `areas`). Every `areas` row maps to one ward region; India / Karnataka / Bangalore are seeded. New cities, states and countries are rows, not code. Ranking, title and badge logic is generic over levels. | PO Q54 replacement. `deeds` already resolves `area_id` from the verified location (M3 D39), so the hierarchy hangs off the same lookup and no other module sees coordinates. | §2.2 |
| D66 | **Each counted deed counts where it happened**, once per level: its ward region and every ancestor (city, state, country). A deed with no matched ward (`area_id` null, e.g. outside every ward or an invite deed without a location) starts at the city level (`deeds.fallback_region_code`, seed Bangalore). No home address or home ward is collected. The region is frozen on the ledger row at award time (D50); locations can't be edited. | PO Q54 replacement. | §4.5 |
| D67 | **Ranking and tie-break** (PO Q50, 2026-11-22): rank score desc; then (1) more distinct reporters in the period at that level; (2) more distinct IST days with counted deeds in the period at that level; (3) *later, not in M4:* AI impact scores once the AI is live; (4) otherwise a **shared rank**, and everyone on a shared titled place gets the title. Shared ranks use dense numbering 1, 1, 2 (PO Q58 = B, 2026-11-22; `shared_rank_style` config). | Deterministic and explainable. | §4.5 |
| D68 | **AI shadow tie-break (M4b)** never changes a rank. At finalization, for each shared titled place, it records which user it *would* rank higher and why. It uses only structured evidence (aggregate counts, category mix, evidence levels, shadow impact scores; no names, text or locations) and predefined criteria. It runs 3 times; unless all 3 agree, the result is "insufficient evidence". Results go to `ai_tiebreak_shadow`. The PO decides before M8, with real data and legal advice, whether it may ever be used. | PO Q50 (LLM tie-break assessed, not implemented). | §2.4, §4.7 |
| D69 | **Public profile and leaderboards show no points** (PO Q55, 2026-11-22). The public profile shows Verified Deeds (verified and counted only), Badges total, Hero titles total, Followers, Following and the tier. Leaderboards show rank, name, tier and verified-deed count. Points are visible only to their owner. **The public Self-log Deeds count is removed**: the owner still sees it in My deeds. This changes built M2/M3 code (`GET /api/users/{handle}/deed-counts`, `PublicProfilePage`). | Same rules on profiles and leaderboards; visibility change only, nothing in points or verification changes. | §3, §6, B37/F19 |
| D70 | **Badges and titles are awarded only once final.** A badge is awarded once the deed that crosses its threshold is past its 24 h window; Hero titles only from a finalized month. Both are permanent unless an admin revokes them for fraud (M5). | PO Q53 = A and Q54 replacement. | §4.6 |

### 0.2 PRD rules that are ambiguous or conflict with built M2/M3 behaviour

| #   | PRD rule | Problem | Resolution |
| --- | -------- | ------- | ---------- |
| A1  | §9 "The act is scored once and the other reports reuse that score." | M2 groups reports whatever their category. | **PO Q46 = A:** each counted report is scored on its own; "scored once" now applies only to the later AI score. PRD updated. |
| A2  | §5 "Verified Deeds counts … the same figure the leaderboard uses" | The leaderboard counts deeds after the daily cap, prize-rank-eligible only, per month and region; the profile count (M2 D15) applies only the same-act cap, all time. | Profile count kept as built; PRD wording changed to "using the same same-act rule as the leaderboard". |
| A3  | §9 photo bonus "when the report includes a photo and a description"; Q29 | Photos come after verification; a photo removed by an admin as fake keeps its bonus. | D51; a fake-photo bonus is corrected only by an M5 admin override. PRD updated. |
| A4  | §5 diminishing pair points | Schedule unstated; M2 allows only 1 report per pair per day anyway. | **PO Q47:** per IST month, 1–3 100%, 4–7 75%, 8–15 50%, both roles. |
| A5  | §5 30% reporter concentration vs §12 "at least 3 different reporters" for prizes | Three equal reporters give 33% each. | Still open, for M5/M8 (ledger stores `counterpart_user_id` for it). |
| A6  | §5 daily cap 10 | Which day; does a withdrawal free a slot? | D52. |
| A7  | §9 people-helped caps per category | No values given. | AI input only (`ai_eval.beneficiary_caps`), seed values for calibration. |
| A8  | §12.2 "tier publicly" vs §9 "points shown on the profile" | | **PO Q55:** tier public, points owner-only. PRD updated. |
| A9  | §5 "a reporter can … withdraw a deed" (any time) and the M2 promotion rule | Unlimited withdrawal would reshuffle closed months and titles. | **PO (Q52 replacement):** 24 h after verification; promotion of the next report still applies within it. PRD §5 updated; M2 code and tests change (B37). |
| A10 | §5 "every profile shows two public counts … Self-log Deeds and Verified Deeds" | | **PO Q55:** Self-log Deeds no longer public. PRD updated; M2/M3 code changes (B37, F19). |
| A11 | §6.10 "local leaderboard and monthly Kindness Hero" | One board or several? | **PO Q54 replacement:** ward and city boards and Heroes at launch, state and country configurable. PRD updated. |
| A12 | M3 Q24/Q51: hiding a deed never affects points or leaderboards | A hidden deed still counts on its **ward** board, so a public ward ranking can show that someone often does deeds in an area they hid from the feed. | Kept as decided (PO Q51 = A). Recorded as a privacy note in §9 and for legal review. |

**Open items** (no PO decision needed for M4a; listed for later):
- O1. Effect of account deletion on deeds, points, boards and titles (PRD §5 says deeds are anonymised; for leaderboards M4 leaves deleted users out of non-final boards and shows them as "Deleted user" on final ones).
- O2. A support path for privacy requests after the 24 h window, when a reporter can no longer withdraw: the doer can still hide the deed from the feed, and M7 gives moderators a takedown.

---

## 1. Scope

### In (PRD references)

- **M4a**
  - Rule-based scoring: 10 points for every category, +2 photo bonus, minimum 4 for spam or empty reports (PRD §9, PO Q45 = C).
  - Append-only points ledger with reversal rows (withdrawal and rejection; inside the window also edits and promotion; prize-rank changes) (§9, §10).
  - Caps: same-act (set by M2), doer daily cap 10, reporter daily cap 10, diminishing pair returns (§5, §6.4, §9).
  - Reporter points: 2 per verified report, 1 beyond the same-act cap; never on leaderboards or prize rank (§9).
  - **24 h withdrawal window** in `deeds`, provisional points, monthly finalization (PO, Q52 replacement).
  - **Location hierarchy** (`regions`) and a leaderboard and Kindness Hero for each level: wards and Bangalore at launch (PO, Q54 replacement).
  - Badges, Hero titles, public tier; public profile changes including removing the public self-log count (Q53, Q55).
  - My points, points on the deed page (owner only), leaderboard pages, withdraw-window UI.
- **M4b** (Q57 = A): AI shadow evaluator (evidence caps 40/70/100, structured output in `impact_evaluations`, mode = shadow), the AI shadow tie-break, the admin shadow view. Switched off until legal approval (Q56).

### Out (later milestones)

- Fraud detectors, `FlagCase`, the 30% concentration rule, score overrides, revoking badges and titles, the review queue (M5).
- Notifications for points, badges and titles (M6; M4 emits the events). Challenges (M6).
- Prizes, prize eligibility, payouts (M8). Titles in M4 are recognition only.
- AI going live; AI impact as tie-break rule (3); any real use of the AI tie-break.

---

## 2. Data model (database-specialist)

Migrations: **0019 `deeds_v7_regions`** (deeds module), **0020 `scoring`** (M4a), **0021 `ai_eval`** (M4b). FKs to `users` and `deeds` use `RESTRICT`.

### 2.1 Changes to existing modules

- **`media`:** no schema change. The worker emits `DeedPhotoReady` when a deed photo reaches `ready` (§5).
- **`deeds`:** config v7 (§2.6); regions (§2.2); a withdrawal-window check in `_edits.withdraw_deed`; `DeedOut` gains `withdrawable_until: datetime | null` (verified deeds: `verified_at + window`; pending: null, cancel any time); `DeedCountsOut` loses `self_logged` (the owner's count comes from the self-log list, D69). New read functions in `deeds.service` (§3.1).
- **`core/runtime_config.py`:** `load_version()`. **`core/events.py`:** `unprocessed_before(session, handler_names, cutoff) -> int` counts outbox events with `occurred_at < cutoff` whose subscribed handlers in `handler_names` have no `processed_events` row (used by finalization, D64).

### 2.2 Location hierarchy (`deeds/models.py`, migration 0019)

**`regions`**

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | int PK | |
| kind | enum `country`, `state`, `city`, `ward` | |
| parent_id | int null FK regions | null only for `country`; CHECK `(kind = 'country') = (parent_id IS NULL)` |
| name | varchar(80) | display name ("Koramangala", "Bangalore", "Karnataka", "India") |
| code | varchar(40) unique | stable code, e.g. `IN`, `IN-KA`, `IN-KA-BLR`, `IN-KA-BLR-W123` |
| created_at | timestamptz | |

A trigger checks parent kinds (ward→city→state→country).

**`region_closure`**: `ancestor_id`, `descendant_id`, `depth` (0 = itself). PK `(ancestor_id, descendant_id)`; index `(descendant_id)`. Maintained by the seed migration and by a `deeds` CLI command for adding regions (no HTTP endpoint).

**`areas.region_id`** int NOT NULL FK regions (each of the 369 wards gets its own ward region under Bangalore; backfilled in 0019).

`deeds.service.deed_region(deed_id)` returns the ward region of `area_id`, or the region with code `fallback_region_code` when `area_id` is null. Only region ids and names leave the module, never coordinates (M3 D39).

### 2.3 `scoring` module (`scoring/models.py`, migration 0020)

**`scoring_evidence`** (one row per deed with evidence; monotonic): `deed_id` PK FK, `first_photo_ready_at` (set once; trigger `scoring_evidence_monotonic` forbids changing it once set), `first_photo_id` (no FK; audit), `updated_at`. Corroboration (another verified report of the act by a different reporter) is read live through `deeds.service.same_act_group_view`.

**`impact_evaluations`** (append-only)

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | uuid PK | |
| deed_id | uuid FK deeds | |
| method | enum `rule_based`, `ai` | |
| mode | enum `live`, `shadow` | CHECK `method <> 'ai' OR mode = 'shadow'`; CHECK `method <> 'rule_based' OR mode = 'live'` |
| status | enum `proposed` | M5 adds `approved`, `overridden` as new rows |
| rule_points | int null | rule_based: points before caps and multiplier |
| text_check | enum `ok`, `category_mismatch`, `spam_or_empty` null | rule_based |
| impact_score / raw_score | smallint null | ai: after / before the evidence cap, 0–100 |
| sub_scores | jsonb null | ai: six criteria, each 0–100 |
| evidence_level | enum `text_only`, `photo`, `corroborated` | |
| evidence_cap_applied | bool | |
| impact_explanation | text null | ai, ≤ 1500 chars, admin-only |
| estimated_beneficiaries | int null | ai |
| evaluation_confidence | numeric(3,2) null | ai |
| human_review_required | bool | |
| model_version, prompt_version | varchar null | ai |
| scoring_config_version | int | |
| input_hash | char(64) null | ai |
| created_at | timestamptz | |

**`points_ledger`** (append-only)

| Column | Type | Notes |
| ------ | ---- | ----- |
| id | uuid PK | |
| user_id | uuid FK users | who receives the points |
| deed_id | uuid FK deeds | |
| role | enum `doer`, `reporter` | |
| kind | enum `award`, `decision`, `reversal` | |
| points | int | award > 0; decision 0; reversal = −reversed |
| deed_units | smallint | 1 for a doer award that counts as a counted deed; reversal negates |
| counts_for_prize_rank | bool | CHECK `role <> 'reporter' OR NOT counts_for_prize_rank` |
| region_id | int FK regions | the deed's leaf region, frozen at first decision (D66) |
| reason | varchar(40) | `verified`, `promoted`, `edited`, `prize_rank_changed`, `photo_evidence`, `doer_daily_cap`, `reporter_daily_cap`, `withdrawn`, `rejected`, `counts_for_points_lost` |
| breakdown | jsonb | `{base, text_check, photo_bonus, pair_ordinal, pair_percent, cap}`; numbers only |
| counterpart_user_id | uuid null | reporter on doer rows, doer on reporter rows |
| reverses_entry_id | uuid null FK self, unique | CHECK `(kind = 'reversal') = (reverses_entry_id IS NOT NULL)` |
| verified_at | timestamptz | copied from the deed; provisional until `verified_at + window` |
| earned_on, period_month | date | IST, from `verified_at` (D58); reversal copies |
| scoring_config_version | int | |
| evaluation_id | uuid null FK impact_evaluations | |
| source_event_id | uuid null | |
| created_at | timestamptz | |

Indexes: `(user_id, created_at desc)`; `(deed_id, role)`; `(period_month, region_id, user_id) WHERE role = 'doer' AND counts_for_prize_rank`; `(user_id, earned_on, role)`.

**`month_finalizations`**: `period_month` PK, `cutoff_at`, `finalized_at`, `config_version`, `snapshot_count`, `titles_awarded`. Written once by `finalize_month`; append-only.

**`leaderboard_snapshots`**: `id`, `region_id`, `period_month`, `computed_at`, `config_version`, `final bool`, `entry_count`. Unique `(region_id, period_month) WHERE final`. Non-final snapshots older than the latest two per region are deleted by the refresh job.

**`leaderboard_entries`**: `snapshot_id` FK cascade, `rank int` (shared ranks repeat), `user_id`, `rank_score`, `deed_count`, `doer_points` (internal, never in public responses), `distinct_reporters`, `distinct_days`. PK `(snapshot_id, user_id)`; index `(snapshot_id, rank)`.

**`hero_titles`**: `id`, `period_month`, `region_id`, `level_kind`, `place int`, `title_key` (config title id, e.g. `hero`, `top3`), `user_id`, `snapshot_id`, `awarded_at`, `config_version`, `revoked_at null`, `revoked_reason null`, `revoked_by null`. Unique `(period_month, region_id, user_id)`. A user can hold several titles in one month (ward and city).

**`user_badges`**: `id`, `user_id`, `badge_slug`, `awarded_at`, `config_version`, `trigger_deed_id null`, `revoked_at null`, `revoked_reason null`. Unique `(user_id, badge_slug)`.

### 2.4 M4b tables (migration 0021)

**`ai_eval_runs`**: `id`, `deed_id`, `input_hash`, `status` (`queued`, `running`, `succeeded`, `failed`, `refused`, `skipped`), `skip_reason`, `attempts`, `model`, `prompt_version`, `input_tokens`, `output_tokens`, `cost_micro_usd`, `error_code` (class or HTTP status only), `evaluation_id`, timestamps. Unique `(deed_id, input_hash)`.

**`ai_tiebreak_shadow`** (append-only): `id`, `period_month`, `region_id`, `place`, `user_ids uuid[]` (the tied users), `outcome` (`prefers`, `insufficient_evidence`, `skipped`), `preferred_user_id null`, `criteria jsonb` (per criterion: which user and a short reason code), `runs jsonb` (the 3 run outcomes), `model`, `prompt_version`, `created_at`. Never read by ranking code.

### 2.5 Identity

No change. Reads use `identity.service.public_summaries` and `is_active`.

### 2.6 Config namespaces

**`deeds` v7** (migration 0019; `DeedLimits` gains two keys): `"withdraw_window_hours": 24`, `"fallback_region_code": "IN-KA-BLR"`.

**`scoring` v1** (`scoring/_limits.py:ScoringConfig`):

```json
{
  "timezone": "Asia/Kolkata",
  "category_base_points": {
    "helping_elderly": 10, "animals": 10, "environment": 10, "food_sharing": 10,
    "road_safety": 10, "medical_help": 10, "community": 10, "other": 10
  },
  "min_points": 4,
  "photo_bonus": 2,
  "photo_bonus_window_hours": 24,
  "reporter_points": 2,
  "reporter_points_beyond_cap": 1,
  "doer_daily_cap": 10,
  "reporter_daily_cap": 10,
  "pair_window": "ist_month",
  "pair_roles": ["doer", "reporter"],
  "pair_multipliers": [
    { "from_ordinal": 1, "percent": 100 },
    { "from_ordinal": 4, "percent": 75 },
    { "from_ordinal": 8, "percent": 50 }
  ],
  "text_rules": {
    "min_letters": 10,
    "max_repeated_char_run": 6,
    "spam_patterns": ["(?i)\\b(follow me|subscribe|whatsapp me|dm me)\\b"],
    "category_keywords": { "animals": ["dog", "cat", "cow", "bird", "puppy", "stray"] }
  },
  "rate_me_user": "120/60", "rate_public_ip": "120/60"
}
```

The pair limit of 15 per month stays in `deeds`; ordinals above 15 can't occur. A category missing from `category_base_points` scores as `other` and logs a warning; a test checks the seeds agree.

**`leaderboard` v1** (`LeaderboardConfig`):

```json
{
  "weights": { "deeds": 1, "points": 1 },
  "refresh_seconds": 300,
  "page_size": 50,
  "finalize_margin_minutes": 60,
  "shared_rank_style": "dense",
  "levels": [
    { "kind": "ward", "enabled": true, "min_counted_deeds": 3, "min_distinct_reporters": 2,
      "titles": [ { "key": "hero", "places": [1], "label": "Kindness Hero · {region} · {month}" } ] },
    { "kind": "city", "enabled": true, "min_counted_deeds": 0, "min_distinct_reporters": 0,
      "titles": [ { "key": "hero", "places": [1], "label": "{region} City Kindness Hero · {month}" },
                  { "key": "top3", "places": [2, 3], "label": "Top 3 in {region} · {month}" } ] },
    { "kind": "state", "enabled": false, "min_counted_deeds": 0, "min_distinct_reporters": 0, "titles": [] },
    { "kind": "country", "enabled": false, "min_counted_deeds": 0, "min_distinct_reporters": 0, "titles": [] }
  ],
  "tiers": [
    { "slug": "seed", "label": "Seed", "min_points": 0 },
    { "slug": "sprout", "label": "Sprout", "min_points": 50 },
    { "slug": "tree", "label": "Tree", "min_points": 250 },
    { "slug": "forest", "label": "Forest", "min_points": 1000 }
  ],
  "badges": [
    { "slug": "first_recognised", "label": "First recognition", "kind": "doer_deeds", "threshold": 1 },
    { "slug": "kind_5", "label": "5 deeds", "kind": "doer_deeds", "threshold": 5 },
    { "slug": "kind_25", "label": "25 deeds", "kind": "doer_deeds", "threshold": 25 },
    { "slug": "kind_100", "label": "100 deeds", "kind": "doer_deeds", "threshold": 100 },
    { "slug": "first_witness", "label": "First witness", "kind": "reports", "threshold": 1 },
    { "slug": "witness_10", "label": "10 deeds recognised", "kind": "reports", "threshold": 10 },
    { "slug": "witness_50", "label": "50 deeds recognised", "kind": "reports", "threshold": 50 }
  ]
}
```

Ward minimums are a candidate's counted deeds **in that ward that month** and distinct reporters of those deeds. Boards are always visible; the minimums gate only titles. Tier and badge names, title wording and artwork need PO approval (§10). `shared_rank_style` depends on Q58.

**`ai_eval` v1** (M4b): as Revision 1, plus the tie-break keys:

```json
{
  "enabled": false, "mode": "shadow", "model": "claude-opus-5-5", "effort": "low",
  "prompt_version": "v1", "max_output_tokens": 2000, "send_photos": false,
  "criteria_weights": { "impact": 30, "severity": 20, "beneficiaries": 15, "effort": 15, "sustainability": 10, "selflessness": 10 },
  "evidence_caps": { "text_only": 40, "photo": 70, "corroborated": 100 },
  "beneficiary_caps": { "helping_elderly": 3, "animals": 10, "environment": 50, "food_sharing": 20, "road_safety": 10, "medical_help": 5, "community": 50, "other": 10 },
  "confidence_threshold": 0.6, "daily_max_requests": 500, "monthly_budget_usd": 50,
  "price_per_mtok_usd": { "input": 4.0, "output": 20.0 },
  "max_attempts": 5, "circuit_breaker": { "failures": 10, "pause_minutes": 30 },
  "min_delay_after_verify_minutes": 30,
  "tiebreak": { "enabled": false, "runs": 3, "prompt_version": "tb-v1",
                "criteria": ["evidence_strength", "breadth_of_recognition", "consistency", "impact_scores"] }
}
```

`mode` is `Literal["shadow"]` and `send_photos` is `Literal[False]` (Q56). Secret `GD_ANTHROPIC_API_KEY` is a setting (no key = off; refused in tests; never logged).

### 2.7 Notes

- No scoring table stores a description, coordinates or a phone number.
- Totals are `SUM(points)` per user and role from the ledger; a cache table can be added later and rebuilt from the ledger.

---

## 3. API contract

### 3.1 Conventions and service additions

- Thin routers, `AppError` in services, 404 for non-owners, opaque keyset cursors. Reads need `CurrentUser`; there are no member write endpoints in `scoring`.
- **`deeds.service` additions:** `scoring_inputs(deed_id)` (status, method, reporter, doer, category, description in-process only, beneficiaries, verified_at, same_act_group_id, counts_for_points, prize_rank_eligible); `pair_ordinal(doer_id, reporter_id, verified_at)`; `deed_region(deed_id)`; `region_ancestors(region_id)`; `regions(kind, q)` (names and codes for the ward picker); `revision_time(revision_id)`; `withdraw_window_hours()` (reads config v7, so the window is defined in one place).
- **`scoring.service`** (for M5, M6, M8): `doer_points(user_id, period)`, `active_entries(deed_id)`, `final_snapshot(region_id, month)`, `titles_for(user_ids)`, `badges_for(user_ids)`, `tier_for(user_ids)`.

### 3.2 Shapes

```
PublicScoring { tier: {slug, label}, badges_total: int, titles_total: int,
                badges: [{slug, label}], titles: [{label, period_month, region_name, place}] }
MyScoring = PublicScoring + { doer_points: int, reporter_points: int, provisional_points: int,
                boards: [{region: {id, name, kind}, period_month, rank: int|null, deed_count, final: bool}],
                reporter_today: {used, cap} }
LedgerEntry { id, created_at, deed_id, role, kind, points, reason, provisional: bool,
              provisional_until: datetime|null, category_label, doer: UserSummary|null, reporter: UserSummary|null }
LeaderboardRow { rank, shared: bool, user: UserSummary, tier: {slug, label}, deed_count }
```

No public shape contains points or rank scores (D69).

### 3.3 Endpoints (`scoring`, under `/api`)

| # | Method, path | Auth | Response | Errors |
| - | ------------ | ---- | -------- | ------ |
| 1 | `GET /api/scoring/options` | CurrentUser | tiers, badge labels, enabled levels, `withdraw_window_hours`, caps to explain | |
| 2 | `GET /api/scoring/me` | CurrentUser | `MyScoring` | |
| 3 | `GET /api/scoring/me/ledger?role=&cursor=` | CurrentUser | `{items: LedgerEntry[], next_cursor}` | 400 `invalid_cursor` |
| 4 | `GET /api/scoring/users/{handle}` | CurrentUser | `PublicScoring` | 404 |
| 5 | `GET /api/scoring/deeds/{deed_id}` | doer or reporter of the deed | own row only: `{points, reason, capped, provisional_until}` | 404 for others and pending deeds |
| 6 | `GET /api/leaderboards?region={code}&month=YYYY-MM&cursor=` | CurrentUser | `{region: {name, kind}, month, computed_at, final, items: LeaderboardRow[], me: LeaderboardRow|null, titles: [{place, label, users}] (final only), next_cursor}`; default region = city, month = current IST month | 400 `invalid_month`; 404 unknown region or a disabled level |
| 7 | `GET /api/leaderboards/regions?kind=ward&q=` | CurrentUser | `[{code, name}]`, ward picker (name search) | |
| 8 | `GET /api/leaderboards/heroes?region=&year=` | CurrentUser | final titles per month for a region | |
| 9 | `GET /api/admin/scoring/deeds/{deed_id}` | admin only | ledger rows + evaluations | 404 (also for an admin who is the doer or reporter) |
| 10 | `GET /api/admin/scoring/months/{YYYY-MM}` | admin only | finalization status: cutoff, pending scoring events, finalized_at | |
| 11 | `GET /api/admin/scoring/shadow?from=&to=` (M4b) | admin only | rule vs AI comparison, refusals, spend, tie-break shadow outcomes | 400 |
| 12 | `GET /api/admin/scoring/shadow/deeds?cursor=` (M4b) | admin only | per-deed pairs including descriptions; access audited (`scoring.shadow_viewed`) | |

**Changed elsewhere:**
- `GET /api/users/{handle}/deed-counts` returns `{verified}` only; the `self_logged` field is removed (D69).
- `POST /api/deeds/{id}/withdraw` returns 409 `withdraw_window_closed` ("You can no longer withdraw this deed. It was verified more than 24 hours ago.") for a verified deed past the window, checked under the deed row lock; a pending invite deed is still cancellable at any time. Deed responses carry `withdrawable_until`.

Rate limits: per account and per IP on #2–#8 (`scoring.rate_*`).

### 3.4 Contract clarifications (main session, 2026-11-22, from the F14 build)

1. **#1 options:** `{tiers: [{slug, label, min_points}], badges: [{slug, label, description}], levels: [{kind, enabled}], withdraw_window_hours, doer_daily_cap, reporter_daily_cap, reason_labels: {<reason slug>: <display text>}}`. All values come from config.
2. **#8 heroes:** `{region: {code, name, kind}, items: [{period_month, titles: [{place, label, users: UserSummary[]}]}]}`, final months only, newest first.
3. **#9 admin deed:** `{deed_id, ledger: [LedgerEntry + {breakdown, config_version, reverses_entry_id}], evaluations: [{method, mode, created_at, config_version, scores...}]}`. **#10 admin month:** `{period_month, cutoff_at, pending_scoring_events: int, finalized_at: datetime|null, final_snapshots: int}`.
4. **#4 is public** like `GET /api/users/{handle}/deed-counts` (Q55: tier, badge and title totals are public): no login needed, per-IP rate limit (plus per account when signed in). Still never contains points or ranks. Leaderboards (#6–#8) keep `CurrentUser`.
5. **`MyScoring.total_points`** = doer + reporter points, computed on the server (the client never sums points).
6. **`LedgerEntry.reason_label`**: display text from the server (from `reason_labels`), so the client never maps slugs.
7. **`MyScoring.boards[].region`** = `{id, code, name, kind}` (code added, so cards link to `/leaderboard/{code}`).
8. **D47 refinement:** `scoring` may import exactly one name from `media.service`, the event class `DeedPhotoReady` (needed by `@subscribe`); nothing else from `media`. B34's architecture test enforces this allow-list, so scoring still has no way to read photo state (Q29).
9. **Evidence as of the window end:** `reconcile_deed` reads the deed text as of `verified_at + withdraw_window_hours` (`deeds.service.content_as_of`) and counts the photo bonus only if the evidence was recorded by then, so any later re-score (prize-rank change, late handler) can never pick up a post-window edit or photo.
10. **#6 `finalizes_at`:** the leaderboard response carries `finalizes_at` (the month's earliest finalization time: month end + withdraw window + safety margin, in UTC; `null` once final), so the client never computes cutoffs. #5 also returns `reason_label`.
11. **#1 `levels[]`** = `{kind, enabled, hero_min_deeds, hero_min_reporters, titled_places}` (from `leaderboard` config), so the "no Hero this month" copy can name the minimums.

---

## 4. Rules and processing

### 4.1 Lock order

`reconcile_deed`: advisory `points_doer_day:(doer_id, ist_day)` → advisory `points_reporter_day:(reporter_id, ist_day)` → reads → writes. `finalize_month`: advisory `finalize_month:(period_month)` only, and it never takes the day locks. Refresh takes no locks (a snapshot of committed rows).

### 4.2 `reconcile_deed(deed_id)` (`scoring/_ledger.py`)

1. Load the inputs, the active doer and reporter entries, the evidence and the active config.
2. **Doer target:**
   - Not `verified` or not `counts_for_points` → none (reverse any active award: `withdrawn`, `rejected`, `counts_for_points_lost`).
   - First decision: count distinct deeds with a doer award or decision on `(doer, ist_day)`; at or above `doer_daily_cap` → `decision` row (0, `doer_daily_cap`). Otherwise freeze `pair_ordinal`, `pair_percent` and `region_id`.
   - An existing cap decision stays (D52).
   - Points = round_half_up((10, or `min_points` if `spam_or_empty`, + 2 if photo evidence) × pair_percent / 100), minimum 1. `counts_for_prize_rank = prize_rank_eligible`; `deed_units = 1`.
3. **Reporter target:** verified → 2 if `counts_for_points` else 1; reporter daily cap counted the same way; pair multiplier; `counts_for_prize_rank=false`, `deed_units=0`. Otherwise none.
4. Write reversal + award only where the target differs on `(points, counts_for_prize_rank, deed_units)`. Emit `PointsChanged`.
5. **Content after the window:** the `DeedEdited` handler compares `revision_time(revision_id)` with `verified_at + window`; after the window it does nothing (D56). Status changes after the window come only from M5/M7 (rejection or removal); they write reversals, and finalized boards stay frozen (D64).

Worked example (Q47): Priya reports Ahmed 12 times in November → reports 1–3 earn Ahmed 10 each, 4–7 earn 8 (7.5 rounded up), 8–12 earn 5; Priya earns 2, 2, 2, then 2 (1.5 rounded up) ×4, then 1 ×5.

### 4.3 Photo evidence (`scoring/_evidence.py`)

`DeedPhotoReady(photo_id, deed_id, uploader_id, attached_at)`: if not set and `attached_at ≤ reported_at + photo_bonus_window_hours`, set `first_photo_ready_at` once and reconcile, **if still inside the withdrawal window**. A photo that finishes processing after the window still sets the evidence (it is used by the AI) but doesn't re-score points, so points never move after 24 h. Removal events have no scoring subscriber (test).

### 4.4 Text rules (`scoring/_rules.py`)

As D55; the look-alike folding is copied from `feed/_text.py` into `scoring/_text.py` (scoring can't import feed).

### 4.5 Leaderboards (`scoring/_leaderboard.py`)

- **Refresh** `scoring.refresh_leaderboards` (periodic every minute, acts every `refresh_seconds`): for each open month (the current one, and the previous one until finalized), and each enabled level: join doer prize-rank ledger rows to `region_closure` on `descendant_id = region_id`, filter ancestors of that level's kind, group by `(ancestor, user)`: `SUM(points)`, `SUM(deed_units)`, `COUNT(DISTINCT counterpart_user_id)` over rows with `deed_units > 0`, `COUNT(DISTINCT earned_on)` likewise. Keep users with `deed_count > 0` who are active. Rank score per weights; order by D67; assign shared ranks per `shared_rank_style`. One snapshot per region with entries; regions without entries get no snapshot (the API shows an empty board).
- **Per level, a deed counts once:** each ledger row has one leaf region, and the closure gives exactly one ancestor of each kind.

### 4.6 Finalization, titles, badges (`scoring/_finalize.py`, `_badges.py`)

- **`scoring.finalize_month`** (periodic every 15 min): for the oldest unfinalized past month, `cutoff = first day of next month 00:00 IST + withdraw_window_hours + finalize_margin_minutes`. If `now < cutoff` → wait. If `core.events.unprocessed_before(scoring handler names, cutoff) > 0` → wait (logged; admin #10 shows it). Otherwise, under the per-month advisory lock and if no `month_finalizations` row exists: compute final snapshots for every region of every enabled level (same query, `final=true`), award titles, write `month_finalizations`, emit `MonthFinalized` and `HeroTitleAwarded` events, in one transaction. Re-running does nothing.
- **Titles:** for each final snapshot and each title in the level config: users on the title's places (shared places included) who meet the level's minimums. A ward with no qualifying user gets no title that month (never auto-awarded to the next person); its board stays visible. City: #1 "Bangalore City Kindness Hero", #2–#3 "Top 3 in Bangalore", no minimums. A user can receive a ward title and a city title in the same month.
- **Badges** (`scoring.award_badges`, every 15 min): for users with ledger rows whose `verified_at + window` passed since the last run, count non-provisional counted doer deeds (`SUM(deed_units)` where `verified_at + window ≤ now`) and non-provisional reporter awards; insert missing badges (unique index, idempotent) with the deed that crossed the threshold; emit `BadgeAwarded`. Never removed by later changes (only M5 revocation).
- **Tier:** computed on read from non-provisional all-time doer points.

### 4.7 AI shadow evaluator and tie-break (M4b; `scoring/_ai.py`, `_ai_client.py`, `_ai_tiebreak.py`, queue `ai`)

**Why M4b** (PO Q57 = A): independent of points, adds an outside processor, a secret and a budget that need their own review; the shadow period needs real launch deeds, so it must be ready (switched off) by launch.

**Evaluator.** As Revision 1: triggered for counted verified deeds after `min_delay_after_verify_minutes`, with these steps:
1. Checks: kill switches (config flag, key present, circuit breaker), deed still counted, daily and monthly budget.
2. Builds the redacted input and its hash; idempotent claim.
3. Calls the API: structured output, effort from config, no temperature, SDK retries off.
4. Handles the stop reason (`refusal` → `refused`, no retry; `max_tokens` → one retry) and validates the output.
5. Our code computes the weighted sum, the evidence cap and the review flag, then inserts a shadow evaluation.
6. Retries 429/5xx/timeouts with backoff up to `max_attempts`; fails fast on 4xx and counts it toward the breaker.

**Data sent (PO Q56 = A):**
- *Sent:* the category label, and the description with the doer's and reporter's names and handles, phone numbers, emails and links removed. Also the people-helped count capped per category and the evidence level.
- *Never sent:* photos, locations or area, timestamps, identities, likes, comments, follows or points.
- Third-party names typed in the description may remain (PO accepted).
- Disabled until legal review and approval.

**Tie-break shadow (D68):** at finalization, for each shared titled place, enqueue `ai.tiebreak_shadow(period, region, place)`. Input per tied user, labelled "User A / User B" (no ids or names): counted deeds, distinct reporters, distinct days, category counts, evidence-level counts, and the median and spread of shadow impact scores if any. The model judges the predefined criteria and returns a structured preference per criterion. It is run 3 times; if all 3 agree, `prefers` with the criteria, otherwise `insufficient_evidence`. The result is written to `ai_tiebreak_shadow` and nothing else reads it. It is off unless both `ai_eval.enabled` and `tiebreak.enabled` are set.

**Cost (estimate, 1,500 input + 500 output tokens):** about $0.016 a deed on Opus 5.5, $0.008 on Sonnet 5.5, $0.0004 on Haiku 5.5; at 5,000 counted deeds a month that is about $80 / $40 / $2. Tie-breaks are rare and small. Prompt caching on the fixed system prompt helps; Message Batches is a later lever.

---

## 5. Events

**New, emitted by `media`:** `DeedPhotoReady` (`media.deed_photo_ready`): `photo_id, deed_id, uploader_id, attached_at`.

**Consumed by `scoring`, all ending in `reconcile_deed`:**

| Event | Effect |
| ----- | ------ |
| `DeedVerified` | rules, awards or cap decisions; enqueue AI (M4b) |
| `DeedCountsForPointsChanged` | promotion (inside the window, since promotion only follows a withdrawal) |
| `DeedPrizeRankChanged` | doer row re-written with the new `counts_for_prize_rank` |
| `DeedEdited` | re-score only if the edit was inside the window (D56); AI re-run (M4b) |
| `DeedWithdrawn` | reversals (only possible inside the window) |
| `DeedRejected` | reversals |
| `DeedPhotoReady` | evidence; re-score only inside the window |

Not consumed: photo-removal events and any `feed.*` event (D47).

**New, emitted by `scoring`** (ids and numbers only; M6 subscribes): `PointsChanged` (`user_id, deed_id, role, delta, reason`), `BadgeAwarded` (`user_id, badge_slug`), `HeroTitleAwarded` (`user_id, period_month, region_id, level_kind, place, title_key`), `MonthFinalized` (`period_month`).

**Jobs:** `scoring.refresh_leaderboards` (every minute, acts when due), `scoring.finalize_month` (every 15 min), `scoring.award_badges` (every 15 min); M4b `ai.evaluate_deed`, `ai.requeue_skipped` (nightly), `ai.tiebreak_shadow` (on finalization).

---

## 6. UI flows (frontend-developer)

### 6.1 Files

`src/features/scoring/` (`api.ts`, `types.ts`, `MyPointsPage.tsx`, `LeaderboardPage.tsx`, `WardPicker.tsx`, `TitlesList.tsx`, `BadgeRow.tsx`, `TierChip.tsx`); changes in `src/features/profile/PublicProfilePage.tsx`, the own profile, My deeds and the deed page; `src/features/admin/ShadowPage.tsx` (M4b).

### 6.2 Routes

`/leaderboard` (city board), `/leaderboard/:regionCode` (ward board), `/me/points`, `/admin/scoring/shadow` (admin, M4b).

### 6.3 Screens and states

- **Public profile** (D69): Verified Deeds, Badges (total), Hero titles (total), Followers, Following, tier chip. **No Self-log Deeds count** and no points. Tapping badges or titles opens the list ("Kindness Hero · Koramangala · November 2026").
- **Own profile:** the same, plus "Your points: 124 (12 still provisional)" visible only to you and a link to My points. My deeds keeps the self-log count for the owner.
- **My points:** board cards for the current month ("#4 in Koramangala · provisional", "#31 in Bangalore · provisional"; after finalization "final"), then the history list showing category and names only, never the description:
  - "+10 · Medical / emergency help · recognised by Priya · provisional until 3 Dec, 14:05";
  - "−10 · Report withdrawn";
  - "0 · Daily limit reached: still counts on your profile";
  - "+2 · You recognised Ahmed".

  Explainer: "Points become final 24 hours after a deed is verified."
- **Deed page (reporter):** within the window "You can withdraw until 3 Dec, 14:05" and the Withdraw button; after it the button is gone and "Verified deeds can be withdrawn only in the first 24 hours." Edits still allowed; the edit form notes "Changes after 24 hours don't change points." Pending invites: "Cancel invite" at any time.
- **Deed page (doer or reporter):** their own points line ("+10 points · provisional").
- **Report result screen:** "Ahmed earns points for this deed." when counted; unchanged otherwise.
- **Leaderboard:** level switch (Bangalore / My wards / Find a ward), month selector (past months), a banner "Provisional until 2 Dec, 01:00" or "Final". Rows: rank (shared ranks marked "=2"), avatar, name, tier, "N deeds". The own row is pinned. On final boards, title holders show a badge. A ward board with no qualifying Hero shows "No Kindness Hero this month: a Hero needs at least 3 deeds recognised by 2 different people." Empty, loading and error states.
- **Admin shadow (M4b):** summary tiles, rule-vs-AI table, tie-break shadow list, finalization status.

### 6.4 Frontend rules

Nothing computed client-side; all wording for windows and caps comes from `/api/scoring/options` and `withdrawable_until`. No points on any public screen. Feed cards unchanged. Query keys `['scoring','me']`, `['leaderboard', region, month]`.

---

## 7. Task list

Agents: **DB**, **BE**, **FE**, **QA**, **SEC**, **AD**, **DOC** (main session). All PO answers are in, including Q58 (`shared_rank_style` = dense, 2026-11-22).

| ID | Owner | Task | Depends on | Parallel |
| -- | ----- | ---- | ---------- | -------- |
| DB7 | DB | Migration 0019: `regions`, `region_closure`, parent-kind trigger, seed India/Karnataka/Bangalore + one ward region per `areas` row, `areas.region_id` backfill + NOT NULL; `deeds` v7 config; region CLI in `deeds/cli.py` | – | with B28 |
| B28 | BE | `core`: `runtime_config.load_version`, `events.unprocessed_before`; `deeds.service` additions (§3.1) | – | with DB7 |
| B37 | BE | Deeds changes: withdrawal window in `_edits.withdraw_deed` under the row lock (409 `withdraw_window_closed`; pending invites unaffected); `withdrawable_until` on `DeedOut`; `DeedCountsOut` drops `self_logged`; **update M2/M3 tests** that withdraw verified deeds after 24 h or read `self_logged` (`tests/deeds/test_edits.py`, `test_qa_counts_and_promotion.py`, `test_qa_abuse.py`, `test_qa_privacy.py`, feed profile tests); new window tests | DB7 | with B29 |
| B29 | BE | `media`: emit `DeedPhotoReady`; re-export in `media/service.py` | – | |
| DB8 | DB | Migration 0020: scoring tables (§2.3) with triggers, CHECKs, indexes; seeds `scoring` v1, `leaderboard` v1 | DB7 | |
| B30 | BE | `scoring/_limits.py`, `_text.py`, `_rules.py` | DB8 | |
| B31 | BE | `_evidence.py`, `_ledger.py` (reconcile, locks, caps, multipliers, window rules), `handlers.py`, events | B28, B29, B30, B37 | |
| B32 | BE | `_leaderboard.py` (refresh, levels, closure join, tie-break, shared ranks), `_finalize.py` (cutoff, outbox barrier, titles, minimums), `_badges.py`, tier, jobs | B31 | with B33 |
| B33 | BE | Router + schemas §3.3 #1–#10, rate limits, `scoring.service` | B31 | with B32 |
| B34 | BE | Architecture tests: `scoring` imports neither `feed` nor `media`; `_ledger`, `_rules`, `_leaderboard`, `_finalize` never import `_ai*`; no scoring subscriber for photo-removal or feed events; registry entry | B31 | |
| F14 | FE | Scoring API client and types | B33 contract | with B32 |
| F19 | FE | Public profile (remove the self-log count, add badges/titles totals and tier), own profile, My deeds self-log count from the list; **update `PublicProfilePage.test.tsx`** and related tests | B37, F14 | with F15–F17 |
| F15 | FE | Deed page: withdraw window and copy, edit note, own points line; result screen copy; update M2 withdraw tests | B37, F14 | |
| F16 | FE | My points page | F14 | |
| F17 | FE | Leaderboard pages, ward picker, titles, provisional/final banner | F14 | |
| QA7 | QA | Backend tests per §8 | B31–B34, B37 | |
| QA8 | QA | Frontend tests per §8 | F15–F17, F19 | |
| SEC3 | SEC | Review per §9 (M4a) | QA7, QA8 | |
| **M4b** | | *after the PO reviews M4a* | | |
| DB9 | DB | Migration 0021: `ai_eval_runs`, `ai_tiebreak_shadow`; seed `ai_eval` v1 (`enabled=false`) | M4a merged | |
| B35 | BE | `anthropic` dependency, `GD_ANTHROPIC_API_KEY` setting (secret, log-redacted, refused in tests), fakeable `_ai_client.py`, prompt files v1 and tb-v1 with the PO anchor examples | DB9 | |
| B36 | BE | `_ai.py` evaluator, `_ai_tiebreak.py`, `requeue_skipped`, admin #11–#12 with audit | B35 | |
| F18 | FE | Admin shadow page | B36 contract | |
| QA9 | QA | AI tests with a fake client | B36 | |
| SEC4 | SEC | AI review | QA9 | |
| AD3 | AD | Diagrams: scoring module, region hierarchy, events, finalization flow, AI boundary | SEC3 (and SEC4) | |
| DOC4 | DOC | CLAUDE.md, ROADMAP, PRD after build (the design-level PRD corrections are already applied, §12) | AD3 | |

---

## 8. Test plan (qa-engineer)

### Points

- QR deed verified → doer 10, reporter 2; `earned_on` from server IST time (verified 23:59 vs 00:01 IST fall on different days, and a client `occurred_at` on another day changes nothing).
- Every category gives 10. Spam or empty → 4 and a review flag. Category mismatch → 10.
- Photo bonus +2: attached at +23 h and processed later → bonus (if inside the withdrawal window); photo rejected by the pipeline → none; **removal by the doer, the uploader or an admin → no ledger change**; a second photo gives no second bonus.
- Same-act: 7 reports → 5 doer awards, reporters 2×5 and 1×2; withdrawing a counted report within 24 h → reversal + promotion of the 6th.
- Daily caps (doer 10, reporter 10) at the boundary; a withdrawal doesn't promote the 11th.
- Pair schedule: ordinals 1–3 / 4–7 / 8–15 → 100 / 75 / 50 % for both roles, rounding per the §4.2 example; a new IST month resets; withdrawn earlier deeds don't change later multipliers.
- Edits: category edit inside the window re-scores; the same edit after 24 h updates the text and leaves points unchanged; a late-processed handler for an in-window edit still re-scores.
- Invite deed (with `invites_on`): prize-rank false, then true after a second report, then false when that report is withdrawn.
- Reporter rows can never count for prize rank (CHECK). Ledger append-only; a double reversal is rejected.
- Idempotency and order: duplicate and out-of-order events end in the same ledger. Concurrency at the daily-cap boundary with a barrier.
- Hiding a deed (doer or reporter) changes nothing in points, boards or titles.

### Withdrawal window (deeds)

- Withdraw at 23:59 after verification → ok; at 24:00:01 → 409 `withdraw_window_closed`; the window value comes from config (publish v8 with 1 h in a test).
- Race: withdraw at the boundary concurrently with the window check under the row lock.
- Pending invite can be cancelled after any time. `withdrawable_until` correct and null for pending deeds.
- Updated M2 tests still cover promotion and pair-slot rules inside the window.

### Leaderboards, finalization, titles

- A deed in Koramangala counts once on the Koramangala board and once on Bangalore; a deed with no ward counts only on Bangalore; state/country levels disabled → 404 on those boards.
- Rank score with weights 1/1 and other weights. Tie-break: distinct reporters, then distinct days, then a shared rank; shared #1 both get the title; ranking style per config.
- Ward minimum: 3 deeds by 2 reporters qualifies; 3 deeds from 1 reporter doesn't; nobody qualifies → no ward title, board visible. City has no minimum; #1 and #2–#3 titles; a user gets both a ward and the city title.
- Finalization: not before the cutoff (Nov → 2 Dec 01:00 IST); not while a scoring handler event from before the cutoff is unprocessed; runs once under concurrent workers; final snapshots and titles never change afterwards (a later M5-style rejection writes a reversal but the final board stays).
- Badges are awarded only after the 24 h window of the threshold deed; idempotent; not removed by later reversals.
- Tier from non-provisional points (49/50 boundary).

### API and privacy

- Public shapes (#4, #6, #8, deed counts) contain no points, no rank scores and no `self_logged`; the own endpoints (#2, #3, #5) show points to the owner only; #5 is 404 for others.
- Admin endpoints are admin-only; moderators get 403; a conflicted admin gets 404; the shadow-view access is audited.

### AI shadow (fake client, M4b)

- Kill switches, budget, idempotency, retries, refusal, `max_tokens`, circuit breaker; caps applied by our code (raw 95 → 40 / 70 / 95).
- The exact payload sent contains no doer or reporter name or handle, phone number, email, URL, coordinates, area, timestamp or photo.
- Tie-break: 3 agreeing runs → `prefers`; any disagreement → `insufficient_evidence`; ranks and titles unchanged in every case; input has no ids or names.
- A DB insert of an `ai` row with `mode='live'` fails.

### Frontend

- Public profile without the self-log count and without points; own profile shows points and provisional points; withdraw button disappears after `withdrawable_until`; leaderboard provisional/final banner, shared ranks, ward with no Hero, pinned own row.

---

## 9. Security and privacy concerns (security-reviewer)

- **Points integrity:** no write endpoints; append-only ledger; CHECKs; awards only from events emitted inside the producing transaction; nothing client-controlled reaches `breakdown`, `region_id` or `counts_for_prize_rank`.
- **Window enforcement** is on the server under the deed row lock with server time; the client's `withdrawable_until` is display only.
- **Finalization correctness:** the outbox barrier and the per-month lock; a stuck event blocks finalization rather than finalizing wrong (alert via admin #10).
- **Races:** lock order §4.1; one active entry per `(deed, role)`.
- **Visibility and photo state can't reach points or boards:** architecture tests (D47, D62).
- **Gaming:** category edits only within 24 h; pair multipliers and caps; ward minimums require 2 distinct reporters, which stops one friend crowning someone in a quiet ward; fake-photo bonus bounded at 2 per deed (M5 override).
- **Location privacy (new):** ward boards and ward titles publicly link a person to the wards where their deeds happened, and repeated wins can reveal where someone often is. A deed hidden from the feed still counts on its ward board (PO Q51 = A). Area names are already public on cards, but boards aggregate them. Only the ward name is shown, never finer (M3 Q28). For legal review before launch.
- **Leaderboard enumeration:** boards list display names; rate limits; deleted users excluded from non-final boards.
- **Regex safety** on config patterns; bounded input.
- **AI (M4b):** data sent (asserted in tests), prompt injection (delimited data, schema-bound output, output never public and never used for points or ranks), API key as a secret, no request or response bodies in logs, cost bounded by caps and M2 reporting limits, a tested kill switch, admin-only and audited explanations, cross-border processing (legal).

---

## 10. Before-launch list (additions for ROADMAP)

- PO approval of tier names, badge names and artwork, title wording ("Kindness Hero · {ward} · {month}", "Bangalore City Kindness Hero", "Top 3 in Bangalore").
- Legal (DPDP): AI processor and privacy notice (Q56), cross-border transfer, retention; ward leaderboards and the location-pattern note (§9); then enable `ai_eval` and set `GD_ANTHROPIC_API_KEY` in the production secret store.
- Anthropic organisation: data-retention arrangement (ask about zero data retention), account spend limit, billing alerts.
- PO-supplied anchor set of 30–50 example deeds (including small everyday acts) for AI calibration; shadow-review criteria agreed before the shadow period starts.
- Decide before M8 whether the AI tie-break may ever affect anything, using the recorded shadow outcomes and legal advice.
- Open items O1 (account deletion and boards/titles) and O2 (privacy requests after the withdrawal window).
- Leaderboard copy reviewed so it never implies a cash prize before M8 rules exist.

---

## 11. Questions for the PO

Each question had an example, options and the architect's recommendation; answers are recorded with each (2026-11-21/22).

**Q45. How many points is each deed worth?**
Example: Priya scans Ahmed after he helps an elderly man cross the road and adds a photo; Kavya also scans him.
Options: A. 10 base (12 medical, 8 other), +2 photo, minimum 4, reporter 2 / 1 beyond cap. B. Wide spread by category. C. Same points for every category.
Recommendation: A.
**PO: C (2026-11-22).** Every category gives 10 doer points, +2 photo bonus. Kept from A: minimum 4 for a spam or empty report, reporter 2, and 1 beyond the same-act cap of 5. All values are config.

**Q46. When several witnesses report the same act, whose category and photo count?**
Example: Priya reports Ahmed's help at an accident as "Medical" with a photo; Kavya as "Road & traffic" without.
Options: A. Each counted report scored on its own. B. The act scored once from the first report. C. The act scored once from the best evidence.
Recommendation: A.
**PO: A (2026-11-22).**

**Q47. How should repeat reports between the same two people lose value?**
Example: Priya reports her neighbour Ahmed 12 times in a month.
Options: A. Per IST month, 1–3 full, 4–7 half, 8+ quarter, both roles. B. Rolling 30 days. C. Doer only.
Recommendation: A.
**PO: A, modified (2026-11-22).** Per IST calendar month, doer and reporter: pair reports 1–3 100%, 4–7 75%, 8–15 50%.

**Q48. How many reports per day can earn a reporter points?**
Example: Arjun scans 25 helpers at a community kitchen on Sunday.
Options: A. 10 per IST day. B. 5. C. 20.
Recommendation: A.
**PO: A (2026-11-22).** Reporter points for 10 reports per IST day; doers unaffected.

**Q49. What does the leaderboard cover?**
Options: A. One Bangalore monthly board. B. Ward boards as well. C. Weekly and all-time boards.
Recommendation: A.
**PO: superseded by the hierarchy in Q54 (2026-11-22).** Monthly boards for each ward and for the city, past months viewable.

**Q50. Two people have the same rank score. Who ranks higher?**
Example: Ahmed and Kavya both finish November on 87.
Options: A. More distinct reporters, then who reached it first. B. Who reached it first. C. Shared position.
Recommendation: A.
**PO (2026-11-22):** (1) more distinct reporters in the period; (2) more distinct IST days with counted deeds in the period; (3) later, AI impact scores once the AI is live (not in M4a); (4) otherwise a shared rank, and everyone on a shared titled place gets the title. An LLM tie-break was assessed and **not implemented**. M4b adds it in shadow only (D68): structured evidence, predefined criteria, 3 runs that must agree or the result is "insufficient evidence"; it never changes ranks; to be decided before M8 with real data and legal advice.

**Q51. Should someone be able to stay off the public leaderboard?**
Example: Ahmed hid all his deeds from the feed but has the month's highest score.
Options: A. Everyone with points appears. B. Opt-out setting. C. Opt-in.
Recommendation: B.
**PO: A (2026-11-22).** Everyone with points appears; no leaderboard opt-out. "Hide from feed" stays exactly as built in M3 and never affects points or leaderboards.

**Q52. What happens when points change after a month has closed?**
Example: On 10 December Priya withdraws a November deed after Ahmed was named November's Hero.
Options: A. History changes, the closed board stays. B. Recalculate. C. No withdrawals from closed months.
Recommendation: A.
**PO: replaced by a withdrawal window (2026-11-22).**
- A reporter may withdraw a verified deed only within 24 h of verification (`deeds.withdraw_window_hours`). Pending invites can still be cancelled at any time.
- After the window, edits still update the text but never change points.
- The ledger month is the IST month of verification, from server time.
- Points are shown as provisional to their owner for 24 h.
- Boards are provisional until `finalize_month` runs: after month end + window + margin, and once all earlier scoring events are processed. November finalizes at about 2 Dec 01:00 IST.
- After that, boards and titles are frozen; only confirmed fraud, through admins in M5, changes them.
- Open items: account deletion (O1); a privacy-request path after the window (O2).

**Q53. Which badges do we launch with, and are they permanent?**
Options: A. Starter set, permanent unless revoked for fraud. B. Removed when counts drop. C. Also category badges.
Recommendation: A.
**PO: A (2026-11-22).**
- Doer badges: First recognition, 5 / 25 / 100 deeds.
- Reporter badges: First witness, 10 / 50 deeds recognised.
- Kindness Hero titles.
- Permanent unless an admin revokes one for fraud (M5).
- Awarded only once the deed that counts is past its 24 h window.
- Names and artwork need PO approval.

**Q54. Who is the monthly Kindness Hero?**
Options: A. The city #1 automatically. B. Admin picks. C. One per area.
Recommendation: A.
**PO: replaced by a hierarchical Kindness Hero (2026-11-22).**
- **Levels and boards:** levels are ward/area, city, state and country. Each level has its own monthly board and Hero, taken automatically from final rankings with the Q50 tie-break.
- **Shared ranks and timing:** a shared #1 means everyone on it gets the title. Titles are awarded only after finalization, and one user may win several titles in one month.
- **Display, revocation, prizes:** a title shows level and place, e.g. "Kindness Hero · Koramangala · November 2026". Admins can revoke one for fraud (M5). Cash prizes are M8.
- **Launch:** ward and city levels for Bangalore; state and country levels are configured but switched off. The location hierarchy is data, not code (D65).
- **Where deeds count:** where they happen, once per level (D66). A deed outside mapped wards starts at the city level. No home address or ward is collected.
- **Ward Hero minimum (config per level):** at least 3 counted deeds in that ward that month, from at least 2 distinct reporters. If nobody qualifies there is no ward Hero that month; the board is still visible.
- **City:** #1 "Bangalore City Kindness Hero", #2 and #3 "Top 3 in Bangalore". No minimum; ranked on the whole-city board. Shared ranks apply. The number of titled places per level is config.

**Q55. What is public: the tier, the points number, or both?**
Options: A. Tier, counts and badges public; points owner-only; leaderboards without scores. B. Tier and points everywhere. C. Per-deed tier.
Recommendation: A.
**PO (2026-11-22):**
- **Public profile:** Verified Deeds (verified and counted only), Badges (total), Hero titles (total), Followers, Following (kept public), and the tier (Seed / Sprout / Tree / Forest; names pending approval).
- **Points:** visible only to their owner.
- **Leaderboards:** rank, name, tier and verified-deed count; no points.
- **Self-log Deeds count:** removed from public profiles; the owner still sees it in My deeds.
- **Consistency:** the same rules apply on profiles and leaderboards. Points and verification don't change.

**Q56. What may the AI evaluator see?**
Options: A. Redacted text only. B. Plus blurred photos. C. Nothing until legal.
Recommendation: A.
**PO: A (2026-11-22).**
- **Sent:** category, the redacted description (doer and reporter names, phone numbers, emails and links removed), the people-helped count and the evidence level.
- **Not sent:** photos, exact locations, timestamps or identities. Third-party names may remain.
- **Status:** disabled until legal review and approval. Evidence storage is unchanged.

**Q57. Should the AI shadow evaluator be built in M4?**
Options: A. Slice M4b after review, shipped off. B. With the rest of M4. C. After launch.
Recommendation: A.
**PO: A (2026-11-22).** M4b also hosts the shadow AI tie-break.

**Q58. How are places numbered after a shared rank?**
Example: In November, Ahmed and Kavya share #1 on the Bangalore board, and Sneha has the next score. Both share "Bangalore City Kindness Hero". Is Sneha #2 or #3? With three people sharing #1, does the next person still get "Top 3 in Bangalore"?
Options:
- A. Standard competition ranking (1, 1, 3): Sneha is #3 and gets "Top 3". If three share #1, the next person is #4 and gets no title.
- B. Dense ranking (1, 1, 2): Sneha is #2. If three share #1, the next person is #2 and still gets "Top 3", so up to 5 people may hold city titles.
Recommendation: **A.** Familiar from sport, and the number of people with a title stays close to the number of places. 
**PO: B (2026-11-22): dense ranking 1, 1, 2. If Ahmed and Kavya share #1, Ravi is #2 and the next person #3, so "Top 3" titles can go to more than three people. Seeded as `shared_rank_style: "dense"` (config).**

---

## 12. Corrections made to other documents (2026-11-22)

- **PRD:**
  - §5 "Edit and withdraw" (24 h window, edits after it never change points, invites cancellable any time).
  - §5 "Profile counts" (Self-log Deeds count no longer public; wording on the leaderboard figure).
  - §6.10 and §9 "Contribution points" / "Leaderboard" (points table, per-report scoring, pair schedule, reporter daily cap, photo bonus high-water mark, hierarchical boards and Heroes, tie-break, finalization, public visibility of points).
  - §8 (Region).
  - §12 resolved decisions and limits table.
- **ROADMAP M4:** M4a / M4b split, hierarchy, window, finalization, public profile change.
- **CLAUDE.md:** a note that the `scoring` module is designed in `docs/M4-DESIGN.md`; the append-only list names `points_ledger`, `impact_evaluations`, `month_finalizations`, `ai_tiebreak_shadow`. The full module entry is written after the build (DOC4).
