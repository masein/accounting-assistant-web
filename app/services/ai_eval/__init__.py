"""The AI accountant's evaluation set (roadmap §5.5).

``scenarios.json`` holds fa/en chat scenarios: the user's message, what a
good turn does (tools, cards and their contents, reply language, figures the
reply must quote) and a recorded good trajectory. ``fixture`` seeds the small
company every scenario runs against; ``scoring`` checks one turn against its
expectations; ``runner`` plays the scenarios through the production agent loop
(``run_chat_turn``: real tools, real database, cards cancelled afterwards) with
either a live model or the recorded trajectory, and compares a run with an
earlier one.

Two ways to run it:

* replay — in the test suite, no model and no network: each recorded
  trajectory must still run cleanly through today's tools and score as a
  pass. A tool renamed, an argument dropped or a card that no longer carries
  what the scenario checks fails CI here, before any model sees it.
* live — ``scripts/ai_eval.py`` against the configured model on a scratch
  database, nightly in ``.github/workflows/ai-eval.yml``; a scenario that used
  to pass and now fails is a regression and fails the job.
"""
