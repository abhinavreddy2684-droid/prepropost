# Pre Pro Post: backend delivery plan (MVP-A and MVP-B)

**Status:** foundation v0.1.0, M1.1 and M1.4 done.

## Principles

- Understand, prioritise, split, iterate, analyse at every milestone.
- Risk first: a payments, KYC and legal spike runs alongside MVP-A.
- MVP-A ships without money moving, so real users test whether people accept offers before escrow
  exists.
- Definition of done for every subtask: lint and tests pass, migrations clean, endpoint documented
  in OpenAPI, nothing half-wired.

## MVP-A: discovery marketplace (about 4 weeks)

- **M1 Identity and profiles:** 1.1 auth + email verification (done), 1.2 crafts and locations
  (done), 1.3 talent profile (done), 1.4 recruiter profile and verification (done), 1.5 profile
  completeness (remaining).
- **M2 Media:** 2.1 pre-signed upload and confirm, 2.2 validation and quotas, 2.3 thumbnails,
  2.4 links and craft tagging, 2.5 moderation. Models and likes exist; the upload flow does not.
- **M3 Discovery:** 3.1 filtered list, 3.2 text search and cursor paging, 3.3 talent detail.
  Search logic exists in selectors; endpoints do not. Shortlist is deferred.
- **M4 Projects and offers:** 4.1 projects, 4.2 offers, 4.3 accept/decline/expiry,
  4.4 notifications, 4.5 funnel analytics. Services, events and notifications exist; endpoints do
  not.
- **Keep:** auth and roles, profiles, media upload with strict type and size limits and basic
  thumbnails, filtered search, projects, offers, in-app and email notifications.
- **Defer:** audio/video transcoding (serve originals), advanced ranking, virus scanning (strict
  validation first), funnel dashboards (log events now, report later), phone OTP, shortlists.
- **Dependency:** the React frontend has no recruiter side yet (sign-up, project form, offer form,
  inbox). It must run in parallel or MVP-A can't be demoed end to end.

## MVP-B: money (about 4 to 6 weeks after MVP-A)

> **ASK BEFORE STARTING any of it.**

- **M5 Engagement and escrow:** engagement state machine created on offer acceptance; double-entry
  ledger with a 5% commission snapshot; payment provider in test mode with signed webhooks and
  idempotency; funding, delivery, recruiter approval, talent escalation path.
- **M6 Payouts, disputes, admin:** talent KYC and payout onboarding; release funds and payout job
  (adds a releasing state); refunds, cancellation, dispute workflow; admin console (freeze payouts,
  resolve disputes, moderate media, recruiter revoke/suspend); reconciliation report (ledger vs
  provider).

## Locked product decisions

- **Completion:** recruiter approval only; talent can escalate to admin review after N days of
  silence.
- **Disputes:** admin-mediated. Payouts freeze on dispute, both sides submit evidence, admin
  decides release, refund or split. One appeal.
- **Messaging:** none in the MVP. Contact details revealed only after funding.
- **KYC:** tiered. Email now; recruiter manually approved; talent payout KYC required before
  funding is enabled.
- **Currency:** INR, integer paise.

## M7 Hardening and launch

Rate limits, PII encryption, permission audit; load and concurrency tests, backups, monitoring and
alerts on webhook failures and stuck engagements; terms, privacy policy, compliance sign-off,
staged rollout.

## Parallel workstream (not code)

Choose the payment provider (for example Razorpay Route or Cashfree marketplace); take legal and
chartered-accountant advice on holding user funds, KYC duties, TDS and GST on commission (this plan
is not legal or financial advice); draft terms of service including the circumvention clause and
cancellation policy.

## Open items

- N: days before a talent can escalate a silent recruiter.
- Cancellation and refund rules before and after work starts.
- Which payment provider (shapes the M5 design).
- Whether contact details are revealed only to the recruiter and talent involved, or more widely,
  after funding.

## Analysis checkpoints

After each milestone, a short retrospective: what was harder than expected, what to revisit, debt
taken.

- **After MVP-A:** profile completion rate, upload success rate, search latency, offer acceptance
  rate, time-to-accept.
- **After MVP-B:** ledger reconciliation, payout success rate, dispute rate, share of discoveries
  that become funded hires.
