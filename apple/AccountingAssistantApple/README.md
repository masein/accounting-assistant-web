# AccountingAssistantApple

Beautiful Apple-native chat clients for your accounting backend, covering:

- iOS (`AccountingAssistant iOS`)
- macOS (`AccountingAssistant macOS`)
- watchOS (`AccountingAssistant watchOS`)

The app is intentionally chat-first, with animation-heavy UI and quick command chips. It can:

- Send plain chat messages to `/transactions/chat`
- Upload image/PDF attachments to `/transactions/attachments`
- Use live voice-to-text (iOS/macOS)
- Save AI-generated voucher drafts directly to `/transactions`
- Render business charts inline in chat via commands:
  - `/dashboard`
  - `/ledger`
  - `/invoices`
  - `/missing`

## Backend URL

Default backend URL is set to:

`http://192.168.200.240:1234`

You can change it from inside the app header field.

## Open in Xcode

1. Open:
   `/Users/masein/Developer/accounting-assistant/apple/AccountingAssistantApple/AccountingAssistantApple.xcodeproj`
2. Pick one target/scheme:
   - `AccountingAssistant iOS`
   - `AccountingAssistant macOS`
   - `AccountingAssistant watchOS`
3. Run.

## Regenerate Xcode project

If you add/move files, regenerate with:

```bash
cd /Users/masein/Developer/accounting-assistant/apple/AccountingAssistantApple
ruby Scripts/generate_xcodeproj.rb
```

## Notes

- watchOS UI is intentionally compact and command-focused.
- Voice capture uses Speech + microphone permissions.
- Charts are powered by Apple's `Charts` framework.
