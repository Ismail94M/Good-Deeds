# Good Deeds: Product Requirements v1

**Status:** Approved 2026-11-11. Launch city: Bangalore.

> Make kindness visible. Make goodness contagious.

This document is the source of truth for v1 product behaviour. Sections 14–15 were added after approval: the chosen tech stack and compliance notes.

## 1. Vision, mission, principle

- **Vision:** A world where everyday kindness is seen, celebrated and copied, so goodness becomes contagious.
- **Mission:** Make real acts of kindness visible by letting communities recognize the people who do them, starting in one city and growing city by city.
- **Motive:** Do good. Share good. Enjoy the rewards.
- **Core principle:** Rewards come from being recognized by others. The feed shows only deeds reported by someone else. Points, leaderboard rank and prizes come only from deeds verified by someone else. Self-logged deeds stay on the user's own profile.
- **Product type:** A social feed of good deeds, like Instagram or Facebook, but with only acts of kindness. No entertainment or unrelated posts. Ads appear only as clearly labelled slots between posts, never inside a deed post.
- **Tagline:** Make kindness visible. Make goodness contagious.

## 2. Target audience (v1)

- **Launch market:** one city, Bangalore.
- **Witnesses / reporters:** residents who see kindness and want to recognize it.
- **Doers:** residents who do good and carry a personal QR code / unique ID in the app.
- **Viewers:** people who scroll the feed to see, like, comment on and share good deeds.

## 3. Primary user problems

1. Kindness goes unseen. There is no dedicated, positive place to recognize it.
2. On general social media, good deeds compete with entertainment, influencers and self-promotion, so ordinary doers get lost.
3. Claims of good deeds are hard to trust (staged, exaggerated or self-reported).
4. People want to help but lack small, concrete prompts and a reason to act.
5. Doers rarely learn that someone noticed and appreciated them.

## 4. Key decisions from discovery

| Topic      | Decision                                                                                                                                                                                                                                                                                                                            |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Beachhead  | One city: Bangalore                                                                                                                                                                                                                                                                                                                 |
| Consent    | The doer shares a QR code / unique ID and the witness scans it. With no QR/ID, the deed is posted as pending until the doer accepts the invite link within 7 days.                                                                                                                                                                  |
| Rewards    | Badges and appreciation for everyone. Monthly cash prizes for top leaderboard positions. Prizes are funded by a fixed launch budget, then by sponsors and a fixed share of monthly revenue, so payouts never exceed income. Only prize-eligible users can win (section 12), and every winner is reviewed by a human before payment. |
| Engagement | Social feed of reported good deeds with likes, comments, shares and follows. "You were recognized" notifications. Monthly kindness challenges.                                                                                                                                                                                      |
| North Star | Unique verified reports per month in the launch city. Each reporter counts at most once per act, and only reports within the same-act cap of 3 count.                                                                                                                                                                               |
| AI         | AI impact score (0–100) for verified deeds only. It never decides whether a deed is genuine. Runs in shadow mode first (section 12).                                                                                                                                                                                                |

## 5. Verification

A deed is verified in one of two ways:

- **a) QR scan:** a witness scans the doer's QR/ID, and time and location are plausible. Verified instantly.
- **b) Invite link:** if the doer is not on the app, the reporter posts the deed and sends a single-use invite link (for example over WhatsApp). The deed stays pending and is not shown with the doer's name. It becomes verified when the doer signs up through the link and accepts it. Links expire after 7 days and the deed then stays unverified.

**Invite link safeguards:**

- The reporter's location and time are recorded when the deed is posted, and must be plausible.
- The invited doer must verify a phone number that is different from the reporter's. The same phone number, device or payout account can never be both reporter and doer.
- A reporter can send at most 5 invites per day.
- An invite-verified deed earns points, but counts toward prize rank only after a second independent report of the same act or a human review.

A user can never verify their own deed.

**Self-logged deeds:** users can log their own good deeds (e.g. planting, beach cleaning) on their own profile only. Self-logged deeds never appear in the feed and do not count toward points, impact score, leaderboard, prizes or the North Star, unless another user later verifies the deed by QR/ID scan, in which case it becomes a normal reported deed.

**Anti-collusion measures:**

- Diminishing points when the same reporter–doer pair repeats.
- Daily cap: at most 10 verified deeds per doer per day earn points. Deeds beyond that still appear on the profile.
- Same-act cap: at most 3 reports of the same act (same doer, within 200 metres and 2 hours) earn the doer points.
- Reciprocal verification (A verifies B and B verifies A within a short period) is flagged.
- Ring detection: small groups who mostly verify each other (for example A→B→C→A) are flagged.
- Reporter concentration: no single reporter can provide more than 30% of a doer's prize-rank points in a period.
- QR tokens rotate, and scans must pass a time and location plausibility check.
- All prize winners are reviewed by a human before payout.

## 6. MVP (v1) features

1. **Sign-up and profile:** email, Google or Facebook login, plus phone number verification.
2. **Personal QR code and unique ID:** generated at sign-up and always one tap away in the app. Witnesses scan it with their phone's browser camera or type the ID manually.
3. **Report a deed about someone else:** scan their QR/ID to verify instantly, or, if the doer is not on the app, post the deed as pending and send a single-use invite link. Add text, optional photo or short video, category, location and count of people helped.
4. **Multiple reports of the same act:** reports about the same doer made within 200 metres and 2 hours of each other are treated as the same act. The first 3 verified reports each count as a deed and earn the doer points. Further reports are saved and shown as extra witnesses but earn the doer no more points. Reporters within the cap earn normal reporter points; reporters beyond the cap earn a very small number of reporter points.
5. **Kindness feed:**
   - Scrollable feed of photo, short video and text posts of good deeds reported by others, like Instagram/Facebook.
   - Like, comment, share (inside the app and out to WhatsApp, Instagram and other apps), follow people, and save posts.
   - Feed tabs: Nearby (Bangalore), Following, Top this week.
   - Each post shows its status: Verified or Pending. Pending posts never show the doer's name.
   - Self-logged deeds never appear in the feed.
   - Likes, comments, shares and follower counts never affect impact score, points, leaderboard rank or prizes.
   - Faces of people being helped are blurred by default. Posts showing vulnerable people need their consent or must hide their identity.
6. **Self-log on profile:** users can log their own deeds on their profile only (see section 5).
7. **Notifications:** "You were recognized", "Your deed was verified", likes and comments, challenge and prize updates.
8. **Monthly kindness challenges**, which can be sponsored.
9. **Impact scoring:** rule-based scoring at launch and AI impact evaluation in shadow mode (section 12), with admin overrides and recorded reasons.
10. **Points ledger, badges, local leaderboard and monthly Kindness Hero.**
11. **Monthly cash prizes** for top leaderboard positions, for prize-eligible users only, with human winner review and payout tracking.
12. **Content moderation:** report a post, AI pre-screening for staged, harmful or unrelated content, human review before removal, user blocking.
13. **Ads and sponsorship:** sponsored monthly challenges and prizes, and clearly labelled ads between feed posts (no more than one in every 8 posts). No ads inside a deed post.
14. **Admin analytics dashboard:**
    - Unique verified reports per week and per month (North Star)
    - Weekly growth and monthly trend of the North Star
    - Unique reporters (users who reported at least one deed about someone else)
    - Unique doers (users with at least one verified deed)
    - People helped (anonymous total count)
    - Verification funnel (submitted → pending → verified → unverified/expired), split by QR scan and invite link
    - Same-act groups: number of groups, reports per group, reports beyond the cap of 3
    - Flagged cases: reciprocal, ring, reporter concentration, invite-link anomalies
    - Daily and monthly active users, time spent in feed, likes/comments/shares per post
    - Ad impressions and revenue, sponsor revenue
    - Prize payouts versus revenue

## 7. Future features (not v1)

- Gifts and vouchers from local businesses, and charity donations made in the doer's name. These need stronger verification, KYC where required, and human approval.
- Local help requests (needs safety design).
- Multi-city expansion and global feed.
- Organization and NGO accounts. Workplace, college and apartment-society editions.
- Native mobile app (Android/iOS).
- Professional recognition / LinkedIn: account linking, shareable achievements, certificates, public verified portfolio, recruiter verification links. Only with explicit opt-in and within LinkedIn API policies. Scores are never used as a hiring score.

## 8. Data model (conceptual, tech-agnostic)

- **User:** id, display name, city/area, verified phone, device fingerprint, account created_at, roles (member, moderator, admin), prize_eligible flag, status
- **DoerCode:** user_id, rotating QR token, created/revoked timestamps
- **Deed:** id, doer_user_id (empty until accepted), reporter_user_id, category, description, media (people helped are blurred or excluded), location (coordinates, shown publicly as coarse area), occurred_at, reported_at, beneficiaries_count (anonymous), status (pending / verified / unverified / rejected / removed), verification_method (qr_scan / invite_accept), same_act_group_id, counts_for_points (true for the first 3 in a group, false after), prize_rank_eligible
- **SameActGroup:** id, doer_user_id, centre location, time window, report_count
- **VerificationEvent:** deed_id, method (qr_scan / invite_accept / moderator), actor_id, location/time evidence, result, timestamp
- **Invite:** id, deed_id, reporter_user_id, single-use token, sent_via, expires_at, accepted_by, accepted_phone, accepted_at
- **SelfLog:** id, user_id, description, media, category, created_at. Profile only, never in the feed.
- **Post:** id, deed_id, media, caption, status (verified / pending), created_at
- **Like:** post_id, user_id, created_at
- **Comment:** post_id, user_id, content, created_at
- **Share:** post_id, user_id, target (in_app / whatsapp / instagram / other), created_at
- **Follow:** follower_id, followed_id, created_at
- **ImpactEvaluation:** deed_id, method (rule_based / ai), impact_score, criterion sub-scores, evidence_level, evidence_cap_applied, impact_explanation, estimated_beneficiaries, evaluation_confidence, human_review_required, evaluation_timestamp, model_version, scoring_config_version, mode (live / shadow), status (proposed / approved / overridden)
- **ScoreOverride / AuditLog:** entity, old value, new value, reason, actor, timestamp. Append-only.
- **ScoringConfig:** version, rule-based points table, AI criterion weights, evidence caps, confidence threshold, active flag
- **PointsLedger:** user_id, deed_id, role (doer / reporter), points, counts_for_prize_rank, reason, config version. Append-only.
- **LeaderboardConfig:** version, formula parameters, area, period
- **FlagCase:** id, type (reciprocal / ring / concentration / invite_anomaly / content), users and deeds involved, status (open / parked / resolved), created_at
- **Prize / PrizePayout:** period, rank, user_id, amount, funding_source (launch_budget / sponsor / revenue_share), review_status, pan_number, payout_kyc_status, tds_amount, paid_at
- **Sponsor / AdPlacement:** sponsor details, linked challenge or prize, placement, period
- **Badge / UserBadge**
- **Challenge / ChallengeParticipation**
- **Report / ModerationCase:** target, reason, status, decision, moderator
- **Notification**
- **ProfessionalLink / ShareConsent:** future, not built in v1

## 9. Scoring methodology

### Launch: rule-based scoring

At launch, points come from fixed rules, not AI:

- Base points by deed category (table in ScoringConfig).
- Simple text rules (keyword / regex checks) to confirm the description matches the chosen category and to flag spam or empty reports.
- A small fixed bonus when the report includes a photo and a description. The bonus does not grow with length or number of photos.

### Later: AI impact score (0–100), verified deeds only

The AI assigns a 0–100 sub-score to each criterion and returns a rationale. The impact score is the weighted sum, using the weights in the active ScoringConfig:

| Criterion                     | Weight |
| ----------------------------- | ------ |
| Impact on beneficiaries       | 30%    |
| Severity of problem addressed | 20%    |
| Number of beneficiaries       | 15%    |
| Effort and commitment         | 15%    |
| Sustainability of impact      | 10%    |
| Selflessness and initiative   | 10%    |

**Fairness rule:** two people who perform the same good deed under similar circumstances receive approximately the same impact score, regardless of social status, wealth or popularity.

**Evidence cap:** the score can never exceed what the evidence supports, so a longer story or a bigger "people helped" number cannot inflate it.

| Evidence                                                                   | Maximum impact score |
| -------------------------------------------------------------------------- | -------------------- |
| Text only                                                                  | 40                   |
| Text + photo or video                                                      | 70                   |
| Text + photo/video + at least one other independent report of the same act | 100                  |

The "people helped" count used for scoring is capped per category, and counts above the cap need a photo or a second report.

### Rules

- Likes, comments, shares, followers and the doer's identity are never given to the model.
- Feed popularity is never used for impact score, points, rank or prizes.
- Every verified deed gets a minimum score, and scoring is calibrated with sample deeds that include small everyday acts.
- Low-confidence or flagged scores go to a review queue (handling is parked; see section 12).
- Same-act cap: reports about the same doer within 200 metres and 2 hours count as one act. Only the first 3 verified reports earn the doer points. The act is scored once and the other reports reuse that score.
- Self-logged deeds are never scored unless another user verifies them.

### Contribution points

- **Doer:** base points by deed category, plus the small fixed photo-and-description bonus.
- **Reporter:** small points per verified deed reported, and very small points for reports beyond the same-act cap. Reporter points have a daily cap.
- **Reporter points never count toward prize rank.** They appear only on the reporter's profile and in badges.

### Leaderboard

**Rank score** = number of counted verified deeds in the period (after the daily and same-act caps) + doer points. The weight of each part is set in LeaderboardConfig. Only deeds marked prize-rank eligible count toward prize positions.

Deed count, points, badges, likes and prize history are shown separately on the profile.

## 10. Evaluation workflow

1. **Submit:** a reporter submits a deed about someone else → basic checks (time, location plausibility, reporter is not the doer, invite limits) → same-act check (same doer within 200 metres and 2 hours of an existing report) → if it matches, link it to that SameActGroup; if the group already has 3 counted reports, mark this one as not earning doer points → deed saved and shown in the feed as Pending.
2. **Verify:** a QR scan verifies instantly. If the doer was invited by link, the deed stays pending (no doer name shown) until the doer signs up through the link, verifies a phone number different from the reporter's, and accepts it. If the link is not accepted within 7 days, the deed becomes unverified and is never scored.
3. **Verified** → DeedVerified event → post marked Verified → rule-based points calculated (live) and AI evaluation run (shadow).
4. **Fraud checks:** reciprocal, ring, concentration and invite-anomaly checks run → anything suspicious creates a FlagCase.
5. **Points** written to the ledger → leaderboard recalculated → notifications sent → analytics updated.
6. **Monthly prizes:** at period end, top prize-eligible positions are calculated → human review of each winner → payout recorded in PrizePayout.
7. **Corrections:** any later override or rejection writes a reversal to the points ledger and an audit entry.

## 11. Architecture (modular, tech-agnostic)

- Build a web application first.
- **Shape:** a modular monolith. One deployable app with strict internal module boundaries that talk only through interfaces and domain events. Any module can be split into its own service later without a redesign.
- The app must be stateless so it can scale horizontally. AI evaluation, fraud checks, notifications and media processing run as background jobs.
- Keep the architecture technology-agnostic until the technology selection phase.
- Prioritize scalability, maintainability, security and low-cost initial deployment.
- Avoid unnecessary infrastructure complexity in v1.

**V1**

- Public web application, mobile-responsive so it works well on phones.
- QR scanning through the phone's browser camera, with manual entry of the unique ID as fallback.
- Web-based administrator dashboard for moderation, prize review and analytics.

**Future**

- Native mobile app (Android/iOS) for faster scanning and push notifications.
- Mobile and web clients must reuse the same backend APIs and business rules.

**Modules:**

| Module                            | Responsibility                                                                     | Emits / consumes                           |
| --------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------ |
| Identity & Profiles               | users, roles, QR codes, phone verification, self-logs                              | —                                          |
| Deed Submission                   | create, edit, same-act grouping                                                    | emits DeedSubmitted                        |
| Verification                      | QR scans, invite links, acceptance, expiry                                         | emits DeedVerified / DeedRejected          |
| Scoring                           | rule-based points (live), AI impact evaluation (shadow, later live), evidence caps | consumes DeedVerified, emits DeedEvaluated |
| Fraud Checks                      | reciprocal, ring, concentration and invite-anomaly detection                       | consumes DeedVerified, emits FlagRaised    |
| Review & Audit                    | flag queue, overrides, append-only audit log                                       | consumes FlagRaised                        |
| Points & Leaderboard              | ledger, badges, configurable rankings                                              | consumes DeedEvaluated                     |
| Feed & Social                     | posts, feed ranking, likes, comments, shares, follows                              | consumes DeedSubmitted, DeedVerified       |
| Challenges                        | monthly kindness challenges, sponsored challenges                                  | —                                          |
| Rewards                           | prize eligibility, monthly prize calculation, winner review, payouts               | consumes leaderboard results               |
| Ads & Sponsorship                 | ad slots, sponsored challenges, sponsor-funded prizes                              | consumes Analytics events                  |
| Notifications                     | all user notifications                                                             | consumes the events above                  |
| Moderation                        | reports, AI pre-screening, takedowns, blocking                                     | —                                          |
| Analytics                         | North Star, engagement, fraud, revenue and payout metrics                          | consumes the events above                  |
| Professional Recognition (future) | placeholder boundary, not built                                                    | —                                          |

## 12. Resolved decisions and open items

**Resolved**

1. Launch city: Bangalore.
2. Public display of scores: show an impact tier publicly; keep the number for leaderboard calculation and admins.
3. Scoring rollout: at launch the leaderboard is ranked by rule-based points (category base points + text rules). The AI runs in shadow mode for the first 4–6 weeks: its scores are recorded and compared, but do not affect points, rank or prizes. It goes live only after the comparison is reviewed.
4. North Star: unique verified reports per month (see section 4).
5. Self-logged deeds: profile only, never in the feed.
6. Reports beyond the same-act cap: reporters still earn very small points.

**Prize eligibility (proposed defaults)**

A user can win a cash prize only if all of these are true:

- Verified phone number and one payout account, not shared with any other user.
- Account at least 30 days old.
- At least 5 prize-rank-eligible verified deeds in the period, from at least 3 different reporters.
- No open flag cases.
- Passed human winner review.

**Default limits (all configurable)**

| Limit                                                     | Default                            |
| --------------------------------------------------------- | ---------------------------------- |
| Verified deeds per doer per day that earn points          | 10                                 |
| Same-act cap                                              | 3 reports within 200 m and 2 hours |
| Invite link expiry                                        | 7 days                             |
| Invites per reporter per day                              | 5                                  |
| Max share of a doer's prize-rank points from one reporter | 30%                                |
| Evidence caps on impact score                             | 40 / 70 / 100                      |
| Minimum account age for prizes                            | 30 days                            |
| Minimum deeds and distinct reporters for prizes           | 5 deeds, 3 reporters               |
| Ads in feed                                               | at most 1 in every 8 posts         |

**Open (parked)**

7. Flagged cases: who reviews them and how they are resolved. For v1, flagged deeds are held out of prize rank until this is decided.
8. Cash prizes: confirm tax and compliance requirements with a chartered accountant before the first payout.

## 13. Revenue model (v1)

- Ads between feed posts, clearly labelled.
- Sponsored monthly challenges and prizes from local businesses.
- Later: organisation editions for companies, colleges and apartment societies.
- Prize pool: fixed launch budget for the first 3 months, then about 30–40% of the previous month's revenue plus sponsor funds.

## 14. Tech stack (chosen 2026-11-11)

- **Frontend:** React + TypeScript (Vite), Tailwind CSS, React Router, TanStack Query. Mobile-responsive web app; QR scanning in the browser camera.
- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 + Alembic, Pydantic v2. Modular monolith.
- **Database:** PostgreSQL 16 + PostGIS.
- **Background jobs:** Procrastinate (Postgres-based queue), fed by a transactional outbox of domain events.
- **Media:** S3-compatible object storage (MinIO locally).
- **AI:** provider-agnostic `ImpactEvaluator` interface; shadow mode at launch.

## 15. Compliance notes (to confirm with legal / CA before launch)

- **Minimum age 18** for v1 accounts. This also simplifies prize eligibility.
- **DPDP Act 2023:** clear consent at sign-up for phone number, location, device fingerprint and photos/videos; a privacy notice; data access and deletion requests.
- **Prize tax:** winnings are subject to TDS (Section 194B). Collect PAN and payout (KYC) details on `PrizePayout` before paying winners.
- **People in photos:** faces of people being helped are blurred by default (§6.5).
