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
| North Star | Unique verified reports per month in the launch city. Each reporter counts at most once per act, and only reports within the same-act cap of 5 count.                                                                                                                                                                               |
| AI         | AI impact score (0–100) for verified deeds only. It never decides whether a deed is genuine. Runs in shadow mode first (section 12).                                                                                                                                                                                                |

## 5. Verification

A deed is verified in one of two ways:

- **a) QR scan or ID + code:** a witness scans the doer's rotating QR code, or types the doer's unique ID together with the 6-digit code shown under it, and time and location are plausible. Verified instantly. A QR token or code is accepted for 30–60 seconds after it is shown. The static ID alone never verifies anyone.
- **b) Invite link:** if the doer is not on the app, the reporter posts the deed and sends a single-use invite link (for example over WhatsApp). The deed stays pending and is not shown with the doer's name. It becomes verified when the doer signs up through the link and accepts it. Links expire after 7 days and the deed then stays unverified.

**Invite link safeguards:**

- The reporter gives the time of the deed (up to 7 days ago) and, optionally, its location. A location, if given, must be accurate enough and inside the service area.
- The invited doer must verify a phone number that is different from the reporter's. The same phone number or payout account can never be both reporter and doer. A shared device is allowed, but at most 15 invites per month can be accepted between the same reporter–doer pair on a shared device.
- Existing members can accept an invite as well as new users.
- A reporter can send at most 5 invites per day.
- An invite-verified deed earns points, but counts toward prize rank only after a second independent report of the same act or a human review.

A user can never verify their own deed.

**Self-logged deeds:** users can log their own good deeds (e.g. planting, beach cleaning) with a category, description, date/time and optional location. Self-logs are private: only the owner sees the details. They never appear in the feed and never count toward points, impact score, leaderboard, prizes or the North Star. A self-log can't be converted into a verified deed; a witness who saw the deed reports it separately through the normal QR or ID + code flow. The owner can edit and delete self-logs.

**Profile counts:** every profile shows two public counts, like followers/following: *Self-log Deeds* and *Verified Deeds*. Verified Deeds counts the user's **counted** verified reports, the same figure the leaderboard uses: 20 witnesses of one act count as 5 (the same-act cap), while 100 reports in separate places or times count as 100. Self-logs are never included in it.

**Monthly reporting limits (PO decision 2026-11-16):** per calendar month (India time, resetting on the 1st), a reporter can submit at most 15 reports about the same doer, and at most 150 reports in total across all doers. Withdrawn reports still use up a slot; failed submissions don't. The app warns the reporter when they are close to either limit.

**Invites are switched off for now** (2026-11-16). Only QR / ID + code reporting is live. The invite rules below apply when invites are switched back on; when they return, an invite-verified deed earns points but needs human review or another independent verification before it counts for prizes.

**Edit and withdraw:** a reporter can edit a deed's category, description and number of people helped (every edit is kept in an append-only history), and can withdraw a deed. Time, location, doer and verification method can't be edited. When a counted report in a same-act group is withdrawn, the oldest uncounted report in the group (from a reporter not already counted) is promoted to count. A withdrawal doesn't free the reporter–doer pair's daily slot.

**Account deletion:** deleting an account anonymises the user's deeds instead of deleting them.

**Anti-collusion measures:**

- Diminishing points when the same reporter–doer pair repeats.
- Daily cap: at most 10 verified deeds per doer per day earn points. Deeds beyond that still appear on the profile.
- Same-act cap: at most 5 reports of the same act (same doer, within 200 metres and 2 hours) earn the doer points.
- Reciprocal verification (A verifies B and B verifies A within a short period) is flagged.
- Ring detection: small groups who mostly verify each other (for example A→B→C→A) are flagged.
- Reporter concentration: no single reporter can provide more than 30% of a doer's prize-rank points in a period.
- QR tokens rotate, and scans must pass a time and location plausibility check.
- All prize winners are reviewed by a human before payout.

## 6. MVP (v1) features

1. **Sign-up and profile:** email, Google or Facebook login, plus phone number verification. Forgot password works through the verified phone: an SMS code, then a new password, and every session is signed out. The reply never reveals whether a number has an account (PO decision 2026-11-17). Before launch: email the owner on every reset, and hold prize claims and payout-detail changes for 7 days after a reset, to protect against recycled numbers and SIM swaps.
2. **Personal QR code and unique ID:** generated at sign-up and always one tap away in the app. Witnesses scan it with their phone's browser camera, or type the ID together with the 6-digit code shown under the QR code.
3. **Report a deed about someone else:** scan their QR/ID to verify instantly, or, if the doer is not on the app, post the deed as pending and send a single-use invite link. Add text, optional photo or short video, category, location and count of people helped.
4. **Multiple reports of the same act:** reports about the same doer made within 200 metres and 2 hours of each other are treated as the same act. The first 5 verified reports each count as a deed and earn the doer points. Further reports are saved and shown as extra witnesses but earn the doer no more points, unless a counted report is withdrawn and the next one is promoted (section 5). Reporters within the cap earn normal reporter points; reporters beyond the cap earn a very small number of reporter points.
5. **Kindness feed:**
   - Scrollable feed of photo, short video and text posts of good deeds reported by others, like Instagram/Facebook.
   - Like, comment, share (inside the app and out to WhatsApp, Instagram and other apps), follow people, and save posts.
   - Feed tabs: Nearby (Bangalore), Following, Top (with a period dropdown: this week, this month, this year).
   - **Following** shows deeds where someone you follow is the doer or the reporter (PO decision Q25, 2026-11-18). Every card clearly labels who did the deed and who reported it, so credit and recognition stay fair.
   - Only verified deeds appear in the feed (PO decision Q23, 2026-11-18). A pending deed (invite link) stays private until the doer accepts it.
   - Self-logged deeds never appear in the feed.
   - Likes, comments, shares and follower counts never affect impact score, points, leaderboard rank or prizes.
   - **Photos (PO decision 2026-11-18):** photos only in v1, at most 4 per post; short videos are deferred to a later milestone. Faces are NOT blurred automatically: doers can proudly show their faces. Anyone can report a photo; an admin decides whether it is removed. The photo pipeline must allow optional privacy features later.
     - **No consent tick for now (PO decision 2026-11-18, replaces the earlier tick):** the upload records who uploaded each photo and when. Revisit before launch with legal advice (children's photos under DPDP).
     - **Thumbnails:** attached photos show as small thumbnails, 4 in a row, on the deed form.
     - **Optional blur:** photos are shown as taken. Only if someone did not agree can the uploader tap "Blur a face" on that person, or remove the photo.
     - **No unblurred original is kept (PO decision Q31, 2026-11-19):** the blur is applied once on the server, and the unblurred photo is deleted as soon as processing ends. Nobody (not the uploader, an admin or support) can get it back from us. The blur is final: to change it, the uploader removes the photo and adds it again from their phone within the 24-hour window; after that, removing the photo is the only option.
     - **The uploader can remove their own photos at any time (PO decision Q30, 2026-11-19);** adding photos stays limited to 24 hours after sending. Removing never changes the deed, its verification, points, the leaderboard or prizes.
     - **Removed photos stop at once (PO decision 2026-11-19):** whether the doer, the uploader or an admin removes a photo, links already shared for it stop working immediately. An admin-removed photo is held privately so it can be restored; a restore gives it new links.
     - **Who reviews photo reports (PO decision Q34, 2026-11-19):** moderators and admins. Roles are given only by the engineering team with a server command, recorded in the audit log with a reason. Nobody reviews a report about a deed they did, reported or uploaded photos to. Moderators get only what is explicitly opened to them; later admin areas are admin-only. Moderator and admin accounts need two-step login before launch.
     - **The uploader is told the outcome (PO decision Q39, 2026-11-19):** once a photo report is decided, the uploader sees the result on that deed in My deeds for 30 days, both "removed" (with the reason and a link to the photo guidelines) and "kept", with a dot on the deed card until they open it. Reports still under review are never shown. The photo-guidelines page text is approved by the PO before launch.
     - **Viewers never see that a photo was removed (PO decision Q33, 2026-11-19):** the deed simply shows its remaining photos (or none), with no placeholder or "removed" label, whoever removed it. Only the uploader is told, in My deeds.
     - **Admin-removed photos are kept for 90 days (PO decision Q32, 2026-11-19):** only admins can see a held photo, and it can be restored during those 90 days. After that it is deleted for good; the record of the decision stays, without the image. The period is a config value.
     - **The doer can remove photos of their deed (PO decision Q29, 2026-11-19):** the doer can remove any photo on a deed about them, whoever uploaded it, without a report or an admin. Only that photo goes; the other photos and the deed stay. The removal is permanent everywhere (feed, deed page, shared page) and links to it stop working at once; the uploader can't put it back. The uploader sees a neutral notice such as "Ahmed removed 1 photo from this deed." An internal audit record (who, when, which deed, no image) is kept. It never changes the deed, who reported it, its verification, points, the leaderboard or prizes. Nobody else gains the right to remove another person's photos.
     - **Reporting:** anyone (members, and visitors on a shared deed page) can report a photo with a reason. A report never hides the photo automatically; every report is recorded and waits for human review by an authorised admin. Review order (PO decision Q44, 2026-11-19): reports about children first; then privacy and harmful or distressing images, with equal priority; then everything else. Only an admin can remove or restore a reported photo. The uploader is told the outcome when the review is done. A photo report never changes the deed, its verification or points. Reporting is rate-limited.
   - **Feed visibility (PO decision 2026-11-16):**
     - Every verified deed is published to the public feed automatically.
     - **Before phone verification (PO decision Q42, 2026-11-19):** a signed-in user who hasn't verified their phone can read the feed, deed pages, comments and profiles. Liking, commenting, saving, following and reporting comments ask them to finish setting up first. Anyone can still report a photo, so safety reports never wait.
     - **Public-feed notice (PO decision Q41, 2026-11-19):** when M3 launches, every user sees a one-time card at the top of the feed: "Deeds people record about you now appear in the public feed with your name and the area. You can hide any of them from the deed's page." It also covers deeds recorded before M3. It shows once per person, on any device.
     - Before submitting a report, the reporter is clearly told that verified deeds appear publicly.
     - The doer or the reporter can hide the deed from the public feed at any time. Each person's hide is separate (PO decision Q24, 2026-11-18): the deed is in the feed only when neither has hidden it, and only the person who hid it can show it again. If Ahmed hides his deed, Priya cannot make it public again, and if Priya hides it, Ahmed cannot either; neither can override the other. Hiding affects public feed visibility only, never verification, counts, points or leaderboard credit.
     - Hiding changes visibility only. It never changes verification status, profile counts, points or prize eligibility.
     - Withdrawn, rejected, flagged or moderator-removed deeds disappear from the feed.
     - Public output shows an area name (e.g. "Koramangala"), never exact GPS coordinates, phone numbers or other private information.
     - **Area names (PO decision Q28, 2026-11-19):** they come from the latest officially published Greater Bengaluru ward boundaries (with a suitable licence; attribution on the Terms page if the licence asks). Each ward shows a familiar, simplified name, never a ward number, and nothing more precise than the ward: no street, building or landmark names, no addresses, no coordinates in any card or API. The deed's location is matched to a ward on our own server and is never sent to an outside map, geocoding or address service. A location outside every ward and more than 1.5 km from the nearest one, or any failure to match, shows just "Bangalore" rather than a guess.
     - Public visibility is stored and checked separately from verification and prize eligibility. Scoring never reads it.
   - **Comments:**
     - Any onboarded member can comment, subject to a length limit and a rate limit (config values) and a basic bad-word filter.
     - **No links or phone numbers in comments (PO decision Q37, 2026-11-19):** a comment containing a link or a phone number is not posted; the person sees a friendly message and can edit it. This stops spam and keeps private numbers off public pages. Each rule can be switched off in config.
     - **Bad-word filter languages (PO decision Q35, 2026-11-19):** English only at M3 launch. Each language has its own word list and rules, and which languages are on is a config setting, so Hindi, Kannada and others can be added later without major code changes. The filter catches common spelling tricks (look-alike characters such as "1d10t", spaced-out or repeated letters). A blocked comment gets the friendly "Please keep it kind" message without naming the word.
     - **Deleting and reporting comments (PO decision Q38, 2026-11-19):**
       - Authors can delete their own comments; the text is erased for good at once.
       - The doer and the reporter can delete any comment on their deed. The comment disappears for everyone at once; its text is kept privately for 30 days (moderators and admins only), then erased.
       - Other members can't delete anyone else's comments.
       - Members can report a comment (from M3; moved forward from M7). A report never hides the comment automatically.
       - Moderators and admins review comment reports and can remove a comment for a policy violation, with a required note. Removed text is kept privately for 30 days, so a mistaken removal can be restored and repeat abuse spotted, then erased; the author deleting a removed comment does not erase it early (PO decision L3, 2026-11-21). If a moderator later restores a comment its author deleted after the removal, it stays deleted as the author's own and its text is erased then. Nobody reviews a report about their own comment or a deed they did or reported.
       - The author of a comment removed by a moderator or admin sees a one-time notice in the app: "Your comment on Ahmed's deed was removed because it broke our community guidelines", with a link to the community guidelines (PO decision 2026-11-19). The notice never names who reported it.
   - **Feed is Home:** for logged-in users the feed is the Home page. Logged-out visitors see a welcome page.
   - **Post card:**
     - The doer comes first, with "recognised by {reporter}" on a smaller line.
     - Then category, area name, time and a "Verified by a witness" badge.
     - Then a photo carousel and the description.
     - Then like, comment, share and save. "People helped" appears only on the detail page.
   - **Saves are private:** listed in a "Saved" list on the user's Profile, with no save count shown and the doer never told.
   - **Top (PO decision 2026-11-19):** one Top tab with a dropdown: **Top this week (last 7 days)**, **Top this month (last 30 days)**, **Top this year (last 365 days)**.
     - The periods are rolling windows, not calendar months or years, and the labels say so.
     - Only deeds published inside the selected window can appear. A deed published earlier never appears, even if it gets new likes or comments.
     - Every period uses the same score: the deed's total likes + 3 × its total comments (the weights and window lengths are config values). Likes from the deed's own doer and reporter count zero in this score (PO decision Q43, 2026-11-19): they can like their deed, and their likes are shown in the like count, but they never move it up in Top. The same holds for their comments (PO decision L1, 2026-11-21): shown and counted in the comment count, zero weight in Top. Equal scores: the newer deed ranks higher.
     - It orders the Top tab only and never affects impact points, verified-deed scores, rank, prizes or the leaderboard. Privacy, moderation and visibility rules apply as everywhere in the feed.
   - **Sharing and public links (PO decision 2026-11-16):**
     - Share targets are WhatsApp, Copy link and the device share menu.
     - A shared link opens a public page for that one deed, showing its photos, description, doer, area and time.
     - **Link previews and search (PO decision Q40, 2026-11-19):** the preview in WhatsApp and other apps is generic (the Good Deeds logo and "A good deed was shared with you"), the same for every deed, with no name, photo or text, and the pre-filled share message is generic too. Public deed pages ask search engines not to list them. Other services keep their own copies of previews and search results, which we couldn't take back after a hide or photo removal.
     - Public links only ever work for verified, visible deeds. They never expose self-logs, unverified or pending deeds, or hidden, withdrawn or removed deeds.
     - Hiding a deed immediately disables its public link.
     - A visitor who isn't logged in can view only that deed. They must sign up to like, comment, follow, or open other profiles and deeds.
     - Public pages show no exact location, phone number or personal information.
   - **Follows:** one-way and open, with no approval needed. Users can remove a follower. Profiles show follower and following counts. **Lists are private (PO decision Q36, 2026-11-19):** other people see only the counts; each person sees their own Followers and Following lists. This can be revisited once blocking exists (M7) or as a privacy setting.
   - **Photos:** up to 4 per report or self-log (a config value). They can be taken with the camera or uploaded.
6. **Self-log on profile:** users can log their own deeds privately, and the profile shows public Self-log Deeds and Verified Deeds counts (see section 5).
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
    - Same-act groups: number of groups, reports per group, reports beyond the cap of 5
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
- **Deed:** id, doer_user_id (empty until accepted), reporter_user_id, category, description, media (photos shown as taken; optional face blur by the uploader), location (coordinates, shown publicly as coarse area), occurred_at, reported_at, beneficiaries_count (anonymous), status (pending / verified / unverified / rejected / removed), verification_method (qr_scan / invite_accept), same_act_group_id, counts_for_points (true for the first 5 in a group, false after, unless promoted after a withdrawal), prize_rank_eligible, withdrawn_at
- **SameActGroup:** id, doer_user_id, centre location, time window, report_count
- **VerificationEvent:** deed_id, method (qr_scan / invite_accept / moderator), actor_id, location/time evidence, result, timestamp
- **Invite:** id, deed_id, reporter_user_id, single-use token, sent_via, expires_at, accepted_by, accepted_phone, accepted_at
- **SelfLog:** id, user_id, category, description, occurred_at, optional location, created_at. Private to the owner; only the count is public. Never in the feed.
- **DeedRevision:** deed_id, changed fields (old and new values), editor, timestamp. Append-only.
- **Post:** id, deed_id, media, caption, created_at (verified deeds only)
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
- Same-act cap: reports about the same doer within 200 metres and 2 hours count as one act. Only the first 5 verified reports earn the doer points. The act is scored once and the other reports reuse that score.
- Self-logged deeds are never scored. A witness reports the deed separately if it should count.

### Contribution points

- **Doer:** base points by deed category, plus the small fixed photo-and-description bonus.
- **Reporter:** small points per verified deed reported, and very small points for reports beyond the same-act cap. Reporter points have a daily cap.
- **Reporter points never count toward prize rank.** They appear only on the reporter's profile and in badges.

### Leaderboard

**Rank score** = number of counted verified deeds in the period (after the daily and same-act caps) + doer points. The weight of each part is set in LeaderboardConfig. Only deeds marked prize-rank eligible count toward prize positions.

Deed count, points, badges, likes and prize history are shown separately on the profile.

## 10. Evaluation workflow

1. **Submit:** a reporter submits a deed about someone else → basic checks (time, location plausibility, reporter is not the doer, invite limits) → same-act check (same doer within 200 metres and 2 hours of an existing report) → if it matches, link it to that SameActGroup; if the group already has 5 counted reports, mark this one as not earning doer points → deed saved. It appears in the feed only once it is verified.
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
- QR scanning through the phone's browser camera, with manual entry of the unique ID plus its rotating 6-digit code as fallback.
- Web-based administrator dashboard for moderation, prize review and analytics.

**Future**

- Native mobile app (Android/iOS) for faster scanning and push notifications.
- Mobile and web clients must reuse the same backend APIs and business rules.

**Modules:**

| Module                            | Responsibility                                                                     | Emits / consumes                           |
| --------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------ |
| Identity & Profiles               | users, roles, QR codes and manual codes, phone verification                        | emits UserRegistered, PhoneVerified        |
| Deeds & Verification              | QR scans, deed create/edit/withdraw, same-act grouping, invite links, acceptance, expiry, self-logs | emits DeedSubmitted, DeedVerified, DeedRejected, DeedEdited, DeedWithdrawn, DeedPrizeRankChanged, DeedCountsForPointsChanged |
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
7. Deed submission and verification are one module (Deeds & Verification), because verification, grouping and acceptance must change a deed in one transaction. It can be split later along its events.
8. Rate limits apply per account and per IP address on every limited action, plus a per-ID limit on typed ID + code entry.

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
| Same-act cap                                              | 5 reports within 200 m and 2 hours |
| Invite link expiry                                        | 7 days                             |
| Reports per reporter–doer pair per calendar month (IST)   | 15 (PO decision 2026-11-16)        |
| Reports per reporter per calendar month (IST), all doers  | 150 (PO decision 2026-11-16)       |
| Photos per report or self-log                             | 4                                  |
| Admin-removed photos kept (restorable) for                | 90 days, then deleted for good (Q32) |
| Report photos can be added after sending for              | 24 hours (removing own photos: any time, Q30) |
| Invites per reporter per day                              | 5                                  |
| Invite deed: how far back it can have happened            | 7 days                             |
| Accepted invites per reporter–doer pair on a shared device | 15 per month                      |
| QR token / manual code accepted for                       | 30 s window + 1 earlier window     |
| Max share of a doer's prize-rank points from one reporter | 30%                                |
| Evidence caps on impact score                             | 40 / 70 / 100                      |
| Minimum account age for prizes                            | 30 days                            |
| Minimum deeds and distinct reporters for prizes           | 5 deeds, 3 reporters               |
| Ads in feed                                               | at most 1 in every 8 posts         |

**Open (parked)**

9. Flagged cases: who reviews them and how they are resolved. For v1, flagged deeds are held out of prize rank until this is decided.
10. Cash prizes: confirm tax and compliance requirements with a chartered accountant before the first payout.

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
- **Media:** S3-compatible object storage: Google Cloud Storage in Mumbai (asia-south1) at launch through its S3-compatible API, and an S3-compatible container locally (the MinIO image is no longer published; see `docs/M3-DESIGN.md`).
- **AI:** provider-agnostic `ImpactEvaluator` interface; shadow mode at launch.

## 15. Compliance notes (to confirm with legal / CA before launch)

- **Minimum age 18** for v1 accounts. This also simplifies prize eligibility.
- **DPDP Act 2023:** clear consent at sign-up for phone number, location, device fingerprint and photos/videos; a privacy notice; data access and deletion requests.
- **Prize tax:** winnings are subject to TDS (Section 194B). Collect PAN and payout (KYC) details on `PrizePayout` before paying winners.
- **People in photos:** no automatic blurring; no consent tick at upload for now (who uploaded and when is recorded); optional blurring of a chosen face; reports reviewed by an admin before any removal (§6.5, PO decisions 2026-11-18). Before launch, confirm with legal whether consent (especially guardian consent for children) must be collected at upload, and the review time limits.
