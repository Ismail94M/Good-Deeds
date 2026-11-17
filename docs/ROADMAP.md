# Good Deeds v1: Build Roadmap

Each milestone is built, tested and reviewed by the product owner before the next one starts. Product rules are in [PRD.md](PRD.md).

**M0 Foundation** ✅ done
- Scaffold `backend/` and `frontend/`, docker-compose, CI.
- Core: config, db, outbox events, jobs, audit log.
- Write `docs/PRD.md`, and rewrite `CLAUDE.md` with real commands.
- PM notes added to the PRD:
  - minimum age 18
  - DPDP Act 2023 consent screens and privacy notice
  - PAN/KYC fields on `PrizePayout` for TDS under Section 194B
- Health-check endpoint, and a web page that calls it.

**M1 Identity & Profiles** ✅ done
- Sign-up and login (email, Google, Facebook).
- Phone OTP with a uniqueness rule: one phone per account.
- Device fingerprint.
- Profile.
- `DoerCode` with a rotating signed QR token plus a permanent short unique ID.
- "My QR" screen.
- Age and consent gate.
- Security hardening from the M1 review:
  - Indian mobile numbers only
  - Postgres rate limits
  - phone history (one number per account, ever)
  - token versioning
  - absolute session lifetime
  - fail-safe production config
  - no automatic OAuth linking
  - nightly data-retention job

**Deferred from M1 (do before launch)**
- CAPTCHA (Turnstile/hCaptcha) on sign-up and phone verification once per-IP thresholds are hit.
- Email verification. Until then, someone can pre-register another person's email with a password; a verified social login for that email then gets `email_exists`.
- Linking a social account from inside a logged-in session.
- A support flow for changing a verified phone number (re-authentication + notify the old number).
- Separate signing keys per purpose (JWT / QR / device / phone hash) with key IDs, so one can be rotated without invalidating the others.
- A real SMS provider (MSG91 or Twilio, with DLT registration).
- Recycled numbers and SIM swaps (PO decision 2026-11-17, forgot-password security review M3): email the account owner on every password reset ("Your password was changed. Wasn't you? Tell us"), once an email service exists. Also hold prize claims and payout-detail changes for 7 days after a reset (built with prizes in M8).

**M2 Deeds & Verification**
- Security requirements from the M1 review:
  - The **manual ID fallback must not be static**: show a short rotating code next to the QR (derived from the same signed window) and require the ID plus that code.
  - Make each `(token window, witness)` and `(doer, witness, day)` single-use, with unique constraints.
  - Location and time plausibility checks are mandatory.
  - Rate-limit QR and ID lookups per witness and per IP.
  - Treat device hashes as a weak signal only; compare reporter and doer against **all** numbers in `phone_history`.
- Report flow: QR scan → instant verify (with time and location plausibility checks), or create an invite (single-use token, 5/day limit, 7-day expiry job).
- Invite acceptance: sign up through the link, phone must differ from the reporter's.
- Same-act grouping (PostGIS, 200 m / 2 h, cap of 5 → `counts_for_points`; a withdrawn counted report promotes the next one).
- Private self-logs, with public Self-log Deeds and Verified Deeds counts on the profile.
- Reporters can edit and withdraw deeds.
- Limits live in versioned config rows (`config_versions`).
- At most 15 reports per reporter–doer pair and 150 per reporter per calendar month (PO decision 2026-11-16, replaces the flat 15). Invites are built but switched off by config (PO decision 2026-11-16).
- Screens designed on Design canvases (F2 My QR, F3 Record a deed, F6 My deeds), approved by the PO on 2026-11-16.
- Events: `DeedSubmitted`, `DeedVerified`, `DeedRejected`, `DeedEdited`, `DeedWithdrawn`, `DeedPrizeRankChanged`, `DeedCountsForPointsChanged`.
- Same-act grouping uses the server's actual scan time; the reporter's "When?" is display only (PO decision 2026-11-17, security L7).
- Forgot password via the verified phone and an SMS code; resetting signs out every session (PO decision 2026-11-17, missed in M1).
- Full decisions: `docs/M2-DESIGN.md`.

**M3 Feed & Social**
- Posts with presigned photo/video upload and a face-blur job.
- Feed tabs: Nearby, Following, Top this week.
- Pending posts hide the doer.
- Likes, comments, shares (Web Share API + WhatsApp link), follows, saves.
- Verified deeds are auto-published. The doer or reporter can hide one from the feed (visibility only). Withdrawn or invalidated deeds leave the feed. Area names only, never GPS. Comments have length and rate limits and a bad-word filter. Open follows (PO decisions 2026-11-16, PRD §6.5).
- Before M3: choose a photo storage provider (Azure Blob / S3 / Google Cloud Storage).

**M4 Scoring, Points & Leaderboard**
- Rule-based scoring: category base points, keyword/regex text rules, fixed photo bonus.
- Ledger with diminishing pair returns, a daily cap of 10, the reporter daily cap, and reporter points excluded from prize rank.
- Configurable rank score.
- Badges and monthly Kindness Hero.
- AI shadow evaluator, with evidence caps 40/70/100 and structured output stored in `ImpactEvaluation` (mode = shadow).
- Public impact tier.

**M5 Fraud & Review**
- Detectors: reciprocal verification, rings (graph cycles over a recent window), 30% reporter concentration, invite anomalies.
- `FlagCase`. Flagged deeds are excluded from prize rank.
- Admin queue, score overrides with a required reason, audit view.

**M6 Notifications & Challenges**
- In-app notifications (plus email).
- Monthly challenges, including sponsored ones, and participation tracking.

**M7 Moderation**
- Report a post.
- AI pre-screen (flags only, never auto-removes).
- Human takedown and user blocking.

**M8 Rewards, Ads & Analytics**
- Prize eligibility rules and monthly prize calculation.
- Hold prize claims and payout-detail changes for 7 days after a password reset (config value; PO decision 2026-11-17).
- Human winner review and payout records, including the funding source and a check that payouts don't exceed the budget.
- Ad slots (at most 1 per 8 posts, labelled) and sponsors.
- Admin analytics dashboard with every metric in PRD §6.14, North Star first.
