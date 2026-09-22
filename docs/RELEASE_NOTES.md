# What's new tour — how releases are announced in-app

Every user gets a short walkthrough of what changed the **first time they log
in after an update**. It is driven by one file: `app/core/release_notes.py`.

## How it works

- `CURRENT_RELEASE` (a date, `YYYY.MM.DD`, optionally `.N` for a second release
  the same day) is compared with `users.last_seen_release` (migration 034).
- `GET /auth/me` returns `whats_new` with the releases the user hasn't seen,
  highlights filtered to their role. `NULL` (a user from before the feature)
  sees only the current release, not the whole history.
- The frontend (`03-ui.js::openWhatsNew`) shows the steps as a modal tour;
  *Show me* opens the relevant page; finishing calls
  `POST /auth/whats-new/seen`. Settings → *What's new* reopens the full
  history (`GET /auth/whats-new`).
- New users are created with `last_seen_release = CURRENT_RELEASE`
  (`provision_company`, admin user create, self-signup) so their first login
  is not a changelog.

## Shipping a release

1. Bump `CURRENT_RELEASE`.
2. Append a `Release(version, date, highlights=(...))` to `RELEASES`. Each
   `Highlight` needs `title` and `body` in **all four** UI languages
   (`en`, `fa`, `es`, `ar`), a `page` that exists in `index.html`
   (`data-page`), optionally `page_by_role` (e.g. personal users →
   `personal-dashboard`) and `roles` to hide notes about pages a role can't
   open.
3. Run `tests/test_whats_new.py` — it fails on a missing language, an unknown
   page, a duplicate key, or a `CURRENT_RELEASE` that isn't the newest entry.

Keep it to the handful of things a user will notice. Internal refactors,
dependency bumps and fixes nobody saw don't belong in the tour.
