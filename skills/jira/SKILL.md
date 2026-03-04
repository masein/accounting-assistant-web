---
name: jira
description: Interact with Jira Cloud through the REST API to triage and manage work. Use when the user asks to authenticate to Jira, search issues with JQL, inspect issue details, create issues, add comments, assign issues, or transition issue status.
---

# Jira

Use this skill to perform reliable Jira operations through `scripts/jira_api.py`.

## Prerequisites

- Export these environment variables before running commands:
  - `JIRA_BASE_URL` (example: `https://your-domain.atlassian.net`)
  - `JIRA_EMAIL` (Atlassian account email)
  - `JIRA_API_TOKEN` (Atlassian API token)
- Verify access first:

```bash
python3 scripts/jira_api.py whoami
```

## Quick Start

```bash
# List my open issues
python3 scripts/jira_api.py search --jql "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"

# Create an issue
python3 scripts/jira_api.py create \
  --project ENG \
  --summary "Fix dashboard timeout" \
  --description "Timeout occurs when loading monthly report." \
  --issue-type Task

# Add a comment
python3 scripts/jira_api.py comment --key ENG-123 --body "Patch is in review."

# Move issue to a new status (find transition ID first)
python3 scripts/jira_api.py transitions --key ENG-123
python3 scripts/jira_api.py transition --key ENG-123 --transition-id 31
```

## Workflow

1. Confirm credentials with `whoami`.
2. Use read operations first (`search`, `get`, `projects`, `transitions`) to gather context.
3. Run write operations only after user intent is explicit (`create`, `comment`, `assign`, `transition`).
4. Return issue keys and concise summaries after each action.

## Script Commands

- `whoami`: Validate auth and print current user.
- `projects`: List visible Jira projects.
- `search --jql ... [--max-results N]`: Query issues with JQL.
- `get --key KEY`: Show details for one issue.
- `create --project KEY --summary TEXT [--description TEXT] [--issue-type TYPE] [--labels a,b] [--assignee ACCOUNT_ID]`
- `comment --key KEY --body TEXT`
- `assign --key KEY --account-id ACCOUNT_ID`
- `transitions --key KEY`
- `transition --key KEY --transition-id ID [--comment TEXT]`

Use `--json` on any command for raw API output when deeper inspection is needed.

## References

- Read `references/jira-auth-and-jql.md` for token setup details and JQL patterns.

## Troubleshooting

- `401 Unauthorized`: Recreate API token and verify `JIRA_EMAIL`.
- `403 Forbidden`: Ensure your account has project permissions.
- `400 Bad Request` on create/comment: Check required fields and issue type availability.
- Empty search results: Validate project key and JQL syntax in Jira UI first.
