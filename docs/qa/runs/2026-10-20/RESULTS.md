# Deep browser test, 2026-10-20

**What was tested:** everything since the 2026-10-16 run, on `main` at #384, with this PR's fixes:
- scanned PDF statements (#381);
- a lunch with a friend, people and wallet names (#382);
- weekly and monthly reviews, suggestions and the nudge (#383);
- "Can I afford it?" (#384).

The setup is the same as before: a fresh scratch database; the three tenants (Arman, `ir`, Persian; Thames, `uk`, English; Sara, personal) built through the UI; Playwright driving `docs/qa/SCENARIOS.md`; and results read back through the API.

**Result: 95 of 95 scenarios pass.** That is the 81 of 2026-10-16, plus 14 in a new group, `scripts/qa/qa_K.py`. Group K follows the reviews from real entries to every place they show. Its seed posts:
- three months of a person's salary and groceries, and a dinner out last week, in new personal books;
- a week of sales and costs at Arman;
- four weeks of fees at Thames.

The scheduler's reviews job then runs on the server for the mornings they need (`qa_reviews_due.py`):
- a Jalali Saturday for the week;
- 1 Mehr for Shahrivar;
- nine quiet days later for the nudge;
- a UK Monday.

The checks:
- **AL1, AL13:** one week, one month and one nudge made. Dining out ran 1,100% above its usual, so the week's idea is a budget whose button carries its figure.
- **AL4, AL17:** three cards in the person's chat:
  - the week 👀 with five bars, the usual line, the categories and the idea;
  - the month 🏆 with its 77% savings ring;
  - the nudge ✏️.

  They aren't said twice, and the conversation's history redraws all three.
- **AL5:** the phone's feed has all three in Persian, and seen in the chat, they leave it. The company owner's bell has the week until the chat shows it.
- **AL2, AL16:** Arman's week reaches the owner in Persian and the CFO in English. A viewer gets none, in the chat or on the phone. Thames's phone briefing brings the week of 12–18 Oct as a `review` block with five bars.
- **AL17-still:** with reduced motion, the figure is whole at once.
- **AL14, AL15:** the owner turns weekly reviews off beside the chat; the accountant's stay on, and the owner turns them back on. Empty books make nothing and say nothing.
- **AK7-web:** a bank renamed on the Entities page takes its account's name with it.

What needs the model is left to pytest and e2e: AJ's scanned pages, AK's cards, AM's answers, and an idea's button turned into a card. The AI is blanked on the QA server.

**Automated UI checks:** about 200 page views across the language, size and tenant combinations, with every section open. **0 page errors.** The flags were:
- the review card's small labels (finding 7);
- raw JSON in the audit trail (finding 6);
- the test's own English file and item names.

**Findings:** eight, in [FINDINGS.md](FINDINGS.md):
- **Fixed in this PR:** five.
  - Three P1s: a review in pounds or Omani rials broke the bell, the chat and the phone's opening briefing (500); books with pence or baisa made no review; "Can I afford it?" raised in Omani rials.
  - The Entities page renamed a bank without its account.
  - The web briefing was said again by each new browser.
- **For the next PR:** three, all of them polish or older than this release.

**The first run** (before the fixes) had 3 failures in group K:
- AL16-en: the phone's briefing answered 500 for Thames;
- AK7-web: the account kept its old name;
- AL5: a harness fault. A person's web home is the chat, which showed the reviews (and marked them seen) two seconds before the test opened the bell.

It also ran into two machine problems:
- an SSH tunnel (not ours) held the host's port 8899, where `qa_up.sh` checked the server's health;
- the Mac slept for over an hour mid-run, after which Docker hung.

The health check now runs inside the container, and the run goes under `caffeinate -i`.

## Screenshots reviewed

- `AL17-week.png`: the Persian week card. 👀, ۹٬۰۰۰٬۰۰۰ with «۲۲۷٪ بیشتر از معمول», five weeks with this one in orange and the dashed usual line, dining out and groceries against their usual, and the budget idea with its button. «معمول» sits over this week's bar (finding 7).
- `AL17-month.png`: Shahrivar 1405. 🏆, ۱۴٬۰۰۰٬۰۰۰ «۱۲٪ کمتر از ماه قبل», the ring at 77% of income, four months of bars, and the categories against the month before.
- `AL13-nudge.png`: «از سه‌شنبه چیزی ثبت نشده — خرجی بوده که بنویسیم؟» with ✏️.
- `AL16-cfo-en.png`: Arman's week in English for the CFO. 🎉 "Revenue this week 110,478,500 IRR ▲ 649% more than usual", with revenue and expense bars. The truncated labels and the mixed-direction sentence are findings 7 and 8.
- `AL5-bell.png`: the owner's bell with «مرور هفتگی آماده است» and the week's headline.
- `I-arman_owner-fa-desktop-audit.png`: the audit trail's JSON details (finding 6).
