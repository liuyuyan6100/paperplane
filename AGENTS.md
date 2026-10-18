# PaperPlane contribution notes

Read README.md and docs/USER_GUIDE.md before changing behavior. Keep runtime data and credentials out of Git. Use synthetic tests and isolated data directories. AI-generated text must be reviewed before adoption. A platform draft is not a public publication. Unknown delivery outcomes require reconciliation before any resend. Preserve version checks and user edits. Never treat API menu/reply video uploads as article-native video.

## Architecture validation branch

Read docs/architecture/README.md and the active milestone before implementation. The selected architecture is a modular monolith; preserve legacy behavior while migrating one vertical slice at a time. New UI state must not be shared through global variables or another feature's DOM. Keep request handlers thin and dependencies explicit.

Keep the sanitized public history and source security checks. Do not merge private development history or bulk-copy a private checkout. Review newer feature patches against docs/architecture/feature-parity.md before migration.

Each installable milestone requires automated regression, a separately identified preview installer and isolated data, then explicit user installation acceptance before the next milestone. Do not claim a proposed framework is implemented, a launched installer is an installed version, or passing synthetic tests prove real platform behavior. Update the milestone record and docs/wiki/README.md links as evidence arrives.
