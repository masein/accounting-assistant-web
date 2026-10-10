# The mobile contract

One contract for the three clients of the AI accountant's replies: the Android app, the web chat and the messenger bots (roadmap `ROADMAP_ANDROID_CHAT.md` P0.10).

- **`mobile-blocks.schema.json`** describes the typed blocks a reply is made of: text, figure, table, proposal, approval, posted, file and intake.
  - Every block has a `type`, an `id` unique in its thread, and a `fallback_text`: a client that can't draw a type shows that sentence.
  - Fields may be added. A client ignores what it doesn't know.
- **`conversations/`** holds recorded replies, one file per conversation, built by the real builders (`app/services/ai_accountant/blocks.py`) from recorded tool results.

## Who checks it

- **`tests/test_mobile_contract.py`** builds every conversation again and checks it:
  - each block against the schema;
  - the result against its recording. A change shows as a list of the fields that differ. Review it, then rewrite the recordings with `CONTRACT_REWRITE=1`, or delete a file to record it afresh.
- **The same test** keeps the table of every `/api/mobile/v1` route and the roles that may call it. A new route, or a changed right, is a deliberate edit there.
- **`android/app/src/test/.../ContractTest.kt`** parses the same recordings and checks that each block becomes the item the screen draws, with its fields. The Android workflow runs when anything here changes.
