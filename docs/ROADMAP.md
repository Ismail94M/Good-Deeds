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
- A real SMS provider (MSG91 or Twilio, with DLT registration). It plugs into the existing `SmsProvider` interface (`app/core/sms.py`); start the DLT registration (sender ID and message templates) weeks early, as approval takes time (PO 2026-11-21).
- Hosting (PO 2026-11-21): Google Cloud Run in Mumbai for the API and the worker (already separate containers), next to the photo bucket. Keep the PostgreSQL job queue (procrastinate) for background jobs and retries; move to Google Cloud Pub/Sub only if traffic ever outgrows it.
- Web server / hosting: send `X-Robots-Tag: noindex, nofollow` on `/d/*` page responses (Q40; the app also adds a robots meta tag, and `/api/public/*` already sends the header). Set `og:image` in `frontend/index.html` to the absolute production URL of `/og-logo.png` (some preview crawlers ignore relative URLs).
- PO approval of the photo-guidelines page copy (M3 Q39).
- PO approval of the community-guidelines page copy (M3 comment-removed notice).
- Two-step login for moderator and admin accounts (they review photo reports from M3, PO decision Q34, 2026-11-19).
- Area names (M3 Q28): get written confirmation from OpenCity / Greater Bengaluru Authority of the ward data's source and terms. PO decision 2026-11-19: use it meanwhile under OpenCity's "Public Domain" label, with a credit on the Terms page. Also confirm no newer ward delimitation and re-check the data file from a browser download.
- Photo consent: confirm with legal whether a consent confirmation (and guardian consent for children) is needed at upload; it was removed from the M3 design for now (PO decision 2026-11-18).
- Recycled numbers and SIM swaps (PO decision 2026-11-17, forgot-password security review M3): email the account owner on every password reset ("Your password was changed. Wasn't you? Tell us"), once an email service exists. Also hold prize claims and payout-detail changes for 7 days after a reset (built with prizes in M8).

**M2 Deeds & Verification** ✅ done
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

**M3 Feed & Social** ✅ done (approved by the PO 2026-11-21; branch `m3-feed`)
- Posts with presigned photo upload: photos only, at most 4 per post; video deferred (PO decision 2026-11-18). No automatic face blurring; no consent tick for now (upload is recorded, revisit with legal before launch); small 4-in-a-row thumbnails; optional "Blur a face"; photo reports always wait for admin review (children first, then privacy and harmful images, then the rest; Q44), only an admin removes or restores, the uploader is told the outcome, and the deed and points are never affected (PRD §6.5). Design approved 2026-11-18: `docs/design/m3-photos`. The doer can remove any photo on a deed about them, permanently, with a neutral notice to the uploader and an audit record; nothing else changes (PO decision Q29, 2026-11-19). The uploader can remove their own photos at any time; adding stays within 24 hours (Q30). Any removal (doer, uploader, admin) stops already-shared photo links at once. **Built:** upload, server-side blur and processing, signed URLs, removal, photo reports and the moderator/admin queue (roles set with the `set-role` CLI, Q34).
- Feed tabs: Nearby, Following, Top. Top has a dropdown: this week / this month / this year, rolling 7 / 30 / 365-day windows over deeds published in the window, likes + 3 × comments, display only (PO decision Q26, 2026-11-19). Self-likes and the doer's and reporter's own comments carry no Top weight (Q43, L1 2026-11-21). **Built.**
- Only verified deeds appear in the feed (PO decision Q23, 2026-11-18); pending invite deeds stay private. **Built.**
- Likes, comments, shares (Web Share API + WhatsApp link), follows, saves. Public share page for one verified, visible deed, with a generic link preview and no search indexing (Q40). **Built.**
- Verified deeds are auto-published. The doer or reporter can hide one from the feed (visibility only). Withdrawn or invalidated deeds leave the feed. Area names only, never GPS: ward-level names from the latest official Greater Bengaluru ward boundaries, matched on our server, "Bangalore" when outside or unmatched (PO decision Q28, 2026-11-19). Comments have length and rate limits and a bad-word filter (English only at launch; per-language packs so Hindi, Kannada and others can be enabled later by config, PO decision Q35, 2026-11-19). Links and phone numbers are refused in comments (Q37). Comment reporting and moderator/admin removal are in M3; authors' deleted comments are erased at once, doer/reporter and moderation deletions keep the text privately for 30 days (Q38, 2026-11-19). Open follows (PO decisions 2026-11-16, PRD §6.5). **Built**, except area names: the lookup works, but the seeded area data is still a placeholder until the approved ward dataset is loaded.
- Photo storage (PO decision 2026-11-18): Google Cloud Storage in the Mumbai region (asia-south1) at launch. The code talks to storage through the provider-neutral S3-compatible interface, so switching provider is a settings change. Local development uses a free S3-compatible stand-in container. **Built** with SeaweedFS in docker-compose; the production GCS bucket and HMAC keys are not set up yet.

**M4 Scoring, Points & Leaderboard** ✅ done (M4a and D71–D76 approved by the PO 2026-11-24; merged to `main`, PR #3; M4b still to build)
- Good Deeds Stories (PO 2026-11-23, D73 in `docs/M4-DESIGN.md`): Instagram-style rings at the top of the home feed for deeds verified in the last 24 hours, blue for reported (witnessed), green for verified (own deed); full-screen viewer; seen state on the server. **Built**, approved.
- Achievement badge medals (PO 2026-11-24, D74 in `docs/M4-DESIGN.md`): badges as medals with ladder icons and colours, bronze/silver/gold rims, details on hover or tap, the next badge locked on my own profile, and a one-time unlock animation. UI only. **Built**, approved.
- Profile stats and Kindness Hero title cards (PO 2026-11-24, D75): icon stat tiles, City and Area titles with their own looks, plus a `level` field on each title in the scoring API. **Built**, approved.
- City ranking open beside Area, with City titles (D76, migration 0025); each person's rank per level on their profile (City Rank beside Verified Deeds, "View all rankings" with Area, City, and State/National "Coming soon"), new read `GET /scoring/users/{handle}/ranks`; full ranking footnote; "Your place" no longer duplicates a visible row. **Built**, approved.
- Rule-based scoring: category base points, keyword/regex text rules, fixed photo bonus. Removing a photo (by the doer, the uploader or an admin) never changes points: the M4 design must make the photo bonus and evidence caps independent of later photo removals (PO decision Q29, 2026-11-19).
- Ledger with diminishing pair returns, a daily cap of 10, the reporter daily cap, and reporter points excluded from prize rank.
- Configurable rank score.
- Badges and monthly Kindness Hero.
- AI shadow evaluator, with evidence caps 40/70/100 and structured output stored in `ImpactEvaluation` (mode = shadow).
- Public impact tier.
- **Design:** `docs/M4-DESIGN.md` (Revision 2; PO answers Q45–Q57 on 2026-11-22). Built in two slices:
  - **M4a:** scoring, ledger, leaderboards, badges, titles and tier.
  - **M4b** (after PO review of M4a): AI shadow evaluator and AI shadow tie-break, shipped switched off until legal review (Q56, Q57).
- Points (Q45–Q48):
  - 10 for every category, +2 photo, minimum 4.
  - Reporter 2 (1 beyond the same-act cap), for at most 10 reports a day.
  - Repeat pairs 100% / 75% / 50% for reports 1–3 / 4–7 / 8–15 in an IST month.
  - Each counted report is scored on its own.
- 24-hour withdrawal window after verification (replaces Q52):
  - Edits after it never change points, and points are provisional for 24 h.
  - Months finalize after month end + 24 h + 1 h, once all earlier events are processed; then boards and titles are frozen.
- Location hierarchy (`regions`: ward, city, state, country) with a monthly board and Kindness Hero per level (replaces Q54):
  - Bangalore wards and city at launch.
  - Ward Hero needs 3 counted deeds from 2 reporters.
  - City #1 Hero, #2–#3 "Top 3 in Bangalore".
  - Tie-break: distinct reporters, then distinct days, then a shared rank (Q50).
- Public profile: Verified Deeds, badges, titles, followers, following and tier. Points are owner-only.
  - The public Self-log Deeds count is removed (Q55). This changes built M2/M3 code.
  - No leaderboard opt-out (Q51).

**M5 Fraud & Review** — **Merged** to `main` (PR #4, 2026-11-27; design `docs/M5-DESIGN.md` rev 4, decisions D77–D102, PO answers Q59–Q85, security review `docs/M5-SEC5.md`). Baseline approach, built to be replaced in parts (D102).
- PO principles: detection alone changes nothing; only an admin confirms, after asking for an explanation; one appeal to a different admin; irreversible steps wait for the appeal.
- Before invites are switched on: the invite-anomaly detector, human review of invite deeds and the SEC3 L7 fix must ship together (D87).
- M8 carries: the 30 % concentration cap in prize ranking, and an open case delays (never denies) a payout (Q61, Q76).
- Detectors: reciprocal verification, rings (graph cycles over a recent window), 30% reporter concentration, invite anomalies.
- `FlagCase`. Flagged deeds are excluded from prize rank.
- Admin queue, score overrides with a required reason, audit view.

**M6 is split into two phases (PO 2026-11-27).** Design: `docs/M6-DESIGN.md`.

**M6A Profile Pictures & In-app Notifications** — **Built**, awaiting PO review (2026-11-28; branch `m6-notifications`; design `docs/M6-DESIGN.md` rev 2, D103–D125, PO answers Q86–Q97; security review `docs/M6-SEC6.md`, 0 high, 1 medium, 7 low, all fixed; migrations 0030–0034). Existing local infrastructure only, no cloud services.
- Profile pictures: upload, crop, preview, replace and remove on mobile and desktop; default avatars; shown consistently on profiles, leaderboards, deeds, recognition activity, reviews and comments; secure validation and storage through the existing media pipeline.
- Social-style in-app notifications: bell, unread count, history, tap-through navigation, read/unread, preferences. Covers recognitions of my deeds, recognitions by people I follow, new followers, deed activity, replies, review decisions and appeal outcomes. Limits against too many notifications; no confidential moderation details (M5 D101).

**M6B Cloud Integration** (after M6A and M6C)
- Email delivery (provider, email verification, the password-changed email deferred from M1).
- Browser push through Firebase Cloud Messaging (FCM), sent from the existing job worker (PO option 2026-11-21).
- Other cloud setup carried from earlier milestones as needed.

**M6C Monthly Challenges** (right after M6A, before M6B; local infrastructure only; PO Q96, 2026-11-27)
- Monthly kindness challenges, including sponsored ones, and participation tracking. Starting design: `docs/M6-DESIGN.md` §13.

**M7 Moderation**
- Report a post. (Comment reporting and removal were moved into M3, PO decision Q38.)
- AI pre-screen (flags only, never auto-removes).
- Human takedown and user blocking.

**M8 Rewards, Ads & Analytics**
- Prize eligibility rules and monthly prize calculation.
- Hold prize claims and payout-detail changes for 7 days after a password reset (config value; PO decision 2026-11-17).
- Human winner review and payout records, including the funding source and a check that payouts don't exceed the budget.
- Ad slots (at most 1 per 8 posts, labelled) and sponsors.
- Admin analytics dashboard with every metric in PRD §6.14, North Star first.
