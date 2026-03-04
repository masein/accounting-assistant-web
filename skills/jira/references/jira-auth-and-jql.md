# Jira Auth and JQL Reference

## Authentication Setup

1. Open Atlassian API token management.
2. Create an API token for your account.
3. Export credentials:

```bash
export JIRA_BASE_URL="https://your-domain.atlassian.net"
export JIRA_EMAIL="you@company.com"
export JIRA_API_TOKEN="your_api_token"
```

4. Validate:

```bash
python3 scripts/jira_api.py whoami
```

## Common JQL Patterns

```text
assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC
project = ENG AND statusCategory != Done ORDER BY priority DESC, updated DESC
project = ENG AND sprint in openSprints() ORDER BY Rank ASC
project = ENG AND text ~ "timeout" ORDER BY created DESC
project = ENG AND created >= -7d ORDER BY created DESC
```

## Useful API Behaviors

- Jira Cloud uses account IDs, not usernames, for assignment.
- Issue create and comment endpoints expect Atlassian Document Format (ADF) for rich text fields.
- Transition IDs are workflow-specific. Always run `transitions --key <ISSUE>` before transitioning.

## Troubleshooting Shortlist

- `401`: wrong token or email.
- `403`: missing project permissions.
- `400`: field missing or invalid issue type for that project.
- `404`: wrong issue key or no permission to view the issue.
