# Mail setup

Connecting the app to the DirectAdmin mailbox on `netixsystem.com` so it can send
account-confirmation links and operator alerts. ~30 minutes, most of it waiting
for DNS.

**You need:** DirectAdmin access, shell access to the deploy host, and the
ability to add DNS records for the domain.

## 0. What this turns on

Two features depend on outgoing mail:

- **Signup confirmation** — a new personal account gets an emailed link it must
  click before it can sign in.
- **Operator alerts** — the business-alerts channel can post to a fixed address
  (`SMTP_TO`).

Until it is configured, mail is simply **off**: the app skips sending rather than
erroring. Nothing breaks if you stop halfway.

> The app only *requires* email confirmation when it can actually send mail. With
> no SMTP configured it signs people straight in — otherwise a brand-new account
> could never be confirmed and the user would be locked out of it. Finishing this
> setup is what switches confirmation on.

## 1. Create the mailbox

DirectAdmin → **Email Manager → Email Accounts** for `netixsystem.com`. Give the
app its own mailbox rather than reusing a person's; `noreply@netixsystem.com` is
the usual choice.

```bash
openssl rand -base64 24     # generate the password, don't invent one
```

Note four things: the outgoing host (usually `mail.netixsystem.com`), the port,
the **full address** as the username, and the password.

> **Most common failure:** on DirectAdmin the SMTP username is the whole address
> (`noreply@netixsystem.com`), not the short form (`noreply`). Getting this wrong
> shows up as an authentication error in step 4.

## 2. DNS — do this before testing

Without these, mail can send *successfully* and still land in spam, which looks
identical to success from the server's side and is far harder to debug later.

DirectAdmin normally creates SPF and DKIM when mail is enabled for a domain.
Confirm they exist, then add DMARC, which it does not create.

```bash
dig +short TXT netixsystem.com | grep spf1        # SPF
dig +short TXT x._domainkey.netixsystem.com       # DKIM (default selector: x)
dig +short TXT _dmarc.netixsystem.com             # DMARC — likely empty
dig +short -x <server-ip>                         # PTR — must not be empty
```

If SPF or DKIM is missing, enable it in **Email Manager → DKIM / SPF** rather
than hand-writing the record — DirectAdmin generates the key and publishes the
zone entry.

Add DMARC as a TXT record, starting in monitoring mode:

| Type | Name | Value |
| --- | --- | --- |
| `TXT` | `_dmarc` | `v=DMARC1; p=none; rua=mailto:postmaster@netixsystem.com` |

Tighten `p=none` to `p=quarantine` after a week of clean reports — a later task,
not part of this setup.

If the PTR lookup returns nothing, ask the hosting provider to set reverse DNS
for the server IP. Many providers reject mail from hosts without one.

## 3. Configure the app

Everything lives in the `.env` next to the compose file you deploy with — see
`.env.prod.example` for the block to copy.

```bash
SMTP_HOST=mail.netixsystem.com
SMTP_PORT=587
SMTP_USER=noreply@netixsystem.com      # the FULL address
SMTP_PASSWORD=…                        # from step 1
SMTP_STARTTLS=true
SMTP_USE_SSL=false
SMTP_FROM=noreply@netixsystem.com
SMTP_FROM_NAME=Accounting Assistant

# The address people actually browse to. Used to build the link inside
# confirmation emails — a localhost URL is useless in an inbox.
APP_PUBLIC_URL=https://your-app-domain

SMTP_TO=ops@netixsystem.com            # optional: operator alerts
```

**If outbound 587 is blocked**, switch to implicit SSL — three lines change:

```bash
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_STARTTLS=false
```

> **Check before restarting.** Production runs a prebuilt image with no `.env`
> inside the container, so these variables only reach the app if the compose file
> passes them through:
>
> ```bash
> grep -c SMTP_HOST docker-compose.prod.yml    # must not be 0
> ```
>
> If it prints `0`, pull the current `docker-compose.prod.yml` first — otherwise
> every setting above is silently ignored and step 4 reports mail as not
> configured.

## 4. Restart and test

Configuration is read at startup.

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f api
```

Signed in to the app as an **owner**, send yourself a test:

```js
await fetch('/admin/test-email', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ to: 'you@example.com' })
}).then(r => r.json())
```

The reply names the cause rather than just failing:

| Response | Meaning |
| --- | --- |
| `ok: true` | Sent — check the inbox *and* the spam folder |
| `"not configured"` | The app never received the settings; see the note in §3 |
| `"username or password"` | SMTP rejected the credentials — usually the short username |
| `"refused the recipient"` | Server won't relay; check `SMTP_FROM` is a real mailbox on the domain |

## 5. Self-signup (optional)

Only if the public should be able to create their own personal-finance accounts.
Skip it entirely for a firm-only deployment.

```bash
ALLOW_SELF_SIGNUP=true
```

With mail working: signup → emailed link, valid 24 hours → click → then sign in.
Self-signup can only ever create a **personal** account, never a business one
with payroll and user management. Test end to end with a real address before
announcing it.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "not configured" | Variables never reached the container | Compose missing the SMTP block, or app not restarted |
| Authentication failed | Short username | Use the full address as `SMTP_USER` |
| Connection times out | Port blocked by host firewall | Try 465 + SSL, or open outbound 587 |
| Sends but lands in spam | SPF / DKIM / DMARC / PTR | Revisit §2; read the received message's headers |
| Confirmation link 404s | `APP_PUBLIC_URL` wrong | Set it to the browsed address, no trailing slash |
| Nothing in the logs at all | Mail disabled — working as designed | `SMTP_HOST` is empty; the app skips sending silently |

Mail failures never break a request: a signup still creates the account if the
email fails, and the user can request a new link. A mail problem is never a
reason to roll back a deploy.

## Checklist

- [ ] Mailbox created; password in the team password manager, not a chat message
- [ ] SPF and DKIM confirmed; DMARC added at `p=none`
- [ ] PTR record exists for the server IP
- [ ] `.env` updated, including `APP_PUBLIC_URL`
- [ ] Compose file confirmed to pass the SMTP variables through
- [ ] App restarted; test email received in an inbox, not spam
- [ ] If self-signup is wanted: enabled and confirmed end to end

---

**Caveat.** The mail code is tested against a stubbed SMTP server; no message has
yet been sent through `netixsystem.com` from this app. Step 4 is the first real
send, so treat an error there as configuration to work through rather than
evidence something is broken.
