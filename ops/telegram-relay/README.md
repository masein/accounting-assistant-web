# Telegram relay

Production can't reach Telegram, which is filtered where the server runs. So the bot's Telegram side runs here, on
a machine that can. [DEPLOY.md §14](../../DEPLOY.md) has the whole picture; this folder is the relay.

The relay polls Telegram for the bot's messages and hands each one to the server (`/bots/telegram/relay/update`,
with the relay key), then sends the server's replies back to Telegram. The bot itself stays on the server: linking a
chat to a login, the same assistant as the web chat, voice notes, and Confirm / Cancel. The bot token lives only
here. The server keeps only the relay key's hash.

## Set it up

1. **Bot:** in Telegram, message @BotFather, send `/newbot`, and keep the token it gives you.
2. **Relay key:** in the app, as the platform admin, go to Settings → Messenger bots → Telegram → **Use a relay**,
   and copy the key it shows. It is shown only once.
3. **On the machine that will run the relay** (it must reach both api.telegram.org and the app), outside the
   repository:
   ```bash
   cp -R ops/telegram-relay ~/telegram-relay && cd ~/telegram-relay
   cp .env.example .env && chmod 600 .env       # TELEGRAM_BOT_TOKEN, APP_URL, RELAY_KEY
   docker compose up -d --build
   docker compose logs -f relay                 # "relaying @YourBot to https://…"
   ```
4. In the app's settings the bot now reads "Connected through the relay as @YourBot". Each user links their own
   chat from the account menu → Telegram, exactly as before.

On the owner's Mac the relay runs from `/Users/masein/Development/accounting/telegram-relay`.

## How it behaves

- **On start:** it removes any webhook the bot had (Telegram allows getUpdates or a webhook, not both) and checks in
  with the server. It checks in again every 10 minutes; the settings page shows when it was last seen.
- **While the server works:** the chat shows "typing…".
- **Order:** one chat's messages are handled in order; different chats at the same time.
- **Voice notes:** fetched here (up to 10 MB) and sent along with the update.
- **Restarts:** the offset of the last update handled is kept in `state/`, so a restart neither loses nor repeats
  updates. The server also ignores an update it has already seen.
- **The server not answering:** each update is tried three times, then dropped and logged.
- **Logs:** the token is never written; httpx's request lines show `<token>` instead.

## When something's wrong

- **`the server doesn't know this relay key`:** relay mode was turned off, or a new key was made. Put the current
  key in `.env`, then run `docker compose up -d`.
- **`Conflict: terminated by other getUpdates request`:** another program is polling the same bot. Only one relay
  may run per bot.
- **No replies, and no errors in the log:** check that the bot's settings page says it's in relay mode, and that
  `APP_URL` is the address users open.

Tests: `tests/test_telegram_relay.py` (this program) and `tests/test_messenger_bot.py` (the server's relay mode).

## Seen working (2026-11-20)

These screens are from @NSAccountingbot through this relay, running on the owner's Mac, against a local test server.
The AI was the stand-in `tests_e2e/fake_llm.py`:

| | |
|---|---|
| ![An unlinked chat gets the instructions](../../docs/screenshots/telegram-relay/1-unlinked-start.png) | ![Linked with the one-time code](../../docs/screenshots/telegram-relay/2-linked.png) |
| 1. `/start` in a chat not linked yet: the instructions | 2. `/start <code>`: linked to the login |
| ![A spend comes back as a card](../../docs/screenshots/telegram-relay/3-card.png) | ![Confirm records it](../../docs/screenshots/telegram-relay/4-confirmed.png) |
| 3. "paid 250000 for lunch from cash": a card with Confirm / Cancel | 4. Confirm: the buttons go, "✅ Done", and the entry is in the books |
| ![Persian, then Cancel](../../docs/screenshots/telegram-relay/5-persian-cancelled.png) | |
| 5. A Persian message gets its card. Cancel: "nothing was saved" | |
