# PaperPlane contribution notes

Public author attribution is 灵魂工程师 (user instruction, 2026-10-23). Preserve third-party credits and historical article/profile signatures. Keep `frontend/src/features/roadmap/roadmap.ts`, ROADMAP.md, acceptance evidence and the in-app guide aligned whenever feature status or priorities change. Record preliminary acceptance separately from complete per-step acceptance; do not invent release dates.

Read README.md and docs/USER_GUIDE.md before changing behavior. Keep runtime data and credentials out of Git. Use synthetic tests and isolated data directories. AI-generated text must be reviewed before adoption. A platform draft is not a public publication. Unknown delivery outcomes require reconciliation before any resend. Preserve version checks and user edits. Never treat API menu/reply video uploads as article-native video.

## Architecture validation branch

Read docs/architecture/README.md and the active milestone before implementation. The selected architecture is a modular monolith; preserve legacy behavior while migrating one vertical slice at a time. New UI state must not be shared through global variables or another feature's DOM. Keep request handlers thin and dependencies explicit.

Keep the sanitized public history and source security checks. Do not merge private development history or bulk-copy a private checkout. Review newer feature patches against docs/architecture/feature-parity.md before migration.

Repository baseline (2026-10-21): the user authorized making `codex/architecture-editor-foundation` the default branch and deleting the former `main` branch reference. Continue from this development baseline; the former main commit remains in its ancestry. This repository setting does not mark M4 complete or publish a stable release. Keep README.md, ROADMAP.md and the Easel comparison consistent with actual implementation and acceptance evidence. Framework images under docs/diagrams are generated with Archify; preserve their source, byte-bound receipts and third-party licenses.

Each installable milestone requires automated regression, a separately identified preview installer and isolated data, then explicit user installation acceptance before the next milestone. Do not claim a proposed framework is implemented, a launched installer is an installed version, or passing synthetic tests prove real platform behavior. Update the milestone record and docs/wiki/README.md links as evidence arrives.

## Feature usage and acceptance guides

The user requested a cumulative teaching and testing guide on 2026-10-20. Maintain docs/feature-guides/README.md for each subsequent acceptance round. Follow its TEMPLATE.md: versioned operating steps with stable IDs and observable expectations, dated records of actual feedback/failures/fixes/retests, and reviewed screenshots with provenance. Keep existing milestone entry links valid; link to authoritative instructions rather than duplicate them. Distinguish synthetic developer evidence, user screenshots, installation, and explicit user acceptance. Never infer whole-stage acceptance from one step. Preserve private runtime evidence outside Git.

The user requires the in-app New User Guide to stay current. Whenever a user-facing feature is added or an entry, step, button label, or completion condition changes, update `frontend/src/features/onboarding/flow.ts`, the visual guide if needed, and `docs/feature-guides/quick-start.md` in the same change. Verify that the guide opens the correct feature without starting paid generation or publication. Keep the guide concise and visual; do not replace it with a long text page.
