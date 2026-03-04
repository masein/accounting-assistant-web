#!/usr/bin/env python3
"""Minimal Jira Cloud CLI helper used by the jira skill."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def _adf_from_text(text: str) -> Dict[str, Any]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        lines = [text.strip() or " "]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            }
            for line in lines
        ],
    }


def _adf_to_text(node: Any) -> str:
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "text":
            return str(node.get("text", ""))
        if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
            return _adf_to_text(node.get("content", [])) + "\n"
        if node_type == "hardBreak":
            return "\n"
        return _adf_to_text(node.get("content", []))
    if isinstance(node, list):
        return "".join(_adf_to_text(item) for item in node)
    return ""


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        errors = []
        error_messages = payload.get("errorMessages") or []
        if isinstance(error_messages, list):
            errors.extend(str(msg) for msg in error_messages if msg)
        field_errors = payload.get("errors") or {}
        if isinstance(field_errors, dict):
            errors.extend(f"{k}: {v}" for k, v in field_errors.items())
        if errors:
            return "; ".join(errors)
    return str(payload)


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{email}:{api_token}".encode("utf-8")).decode("ascii")
        self.common_headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
        }
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "JiraClient":
        return cls(
            base_url=_require_env("JIRA_BASE_URL"),
            email=_require_env("JIRA_EMAIL"),
            api_token=_require_env("JIRA_API_TOKEN"),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            encoded = urllib.parse.urlencode(query, doseq=True)
            url = f"{url}?{encoded}"

        headers = dict(self.common_headers)
        body_bytes = None
        if payload is not None:
            body_bytes = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, data=body_bytes, headers=headers, method=method.upper())

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = response.read().decode("utf-8")
                if not data.strip():
                    return {}
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    return parsed
                return {"data": parsed}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            message = _extract_error_message(parsed)
            raise RuntimeError(f"Jira API {exc.code} {exc.reason}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to connect to Jira: {exc.reason}") from exc

    def whoami(self) -> Dict[str, Any]:
        return self._request("GET", "/rest/api/3/myself")

    def search(self, jql: str, max_results: int, fields: List[str]) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/rest/api/3/search",
            payload={
                "jql": jql,
                "maxResults": max_results,
                "fields": fields,
            },
        )

    def get_issue(self, key: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/rest/api/3/issue/{key}",
            query={"fields": "summary,status,assignee,reporter,description,priority,issuetype"},
        )

    def list_projects(self, max_results: int) -> Dict[str, Any]:
        return self._request("GET", "/rest/api/3/project/search", query={"maxResults": max_results})

    def create_issue(
        self,
        *,
        project: str,
        summary: str,
        issue_type: str,
        description: Optional[str],
        labels: Optional[List[str]],
        assignee: Optional[str],
    ) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            "project": {"key": project},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = _adf_from_text(description)
        if labels:
            fields["labels"] = labels
        if assignee:
            fields["assignee"] = {"accountId": assignee}
        return self._request("POST", "/rest/api/3/issue", payload={"fields": fields})

    def add_comment(self, key: str, body: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/rest/api/3/issue/{key}/comment",
            payload={"body": _adf_from_text(body)},
        )

    def assign_issue(self, key: str, account_id: str) -> Dict[str, Any]:
        return self._request(
            "PUT",
            f"/rest/api/3/issue/{key}/assignee",
            payload={"accountId": account_id},
        )

    def list_transitions(self, key: str) -> Dict[str, Any]:
        return self._request("GET", f"/rest/api/3/issue/{key}/transitions")

    def transition_issue(self, key: str, transition_id: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            payload={"transition": {"id": str(transition_id)}},
        )


def _print_json(data: Dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=True))


def _handle_whoami(args: argparse.Namespace, client: JiraClient) -> None:
    data = client.whoami()
    if args.json:
        _print_json(data)
        return
    display_name = data.get("displayName", "Unknown")
    email = data.get("emailAddress", "N/A")
    account_id = data.get("accountId", "N/A")
    print(f"User: {display_name}")
    print(f"Email: {email}")
    print(f"Account ID: {account_id}")


def _handle_projects(args: argparse.Namespace, client: JiraClient) -> None:
    data = client.list_projects(args.max_results)
    if args.json:
        _print_json(data)
        return
    projects = data.get("values", [])
    if not projects:
        print("No projects found.")
        return
    for project in projects:
        key = project.get("key", "")
        name = project.get("name", "")
        kind = project.get("projectTypeKey", "")
        print(f"{key}\t{kind}\t{name}")
    total = data.get("total")
    if isinstance(total, int):
        print(f"\nCount: {len(projects)} / {total}")


def _handle_search(args: argparse.Namespace, client: JiraClient) -> None:
    fields = [item.strip() for item in args.fields.split(",") if item.strip()]
    data = client.search(args.jql, args.max_results, fields)
    if args.json:
        _print_json(data)
        return
    issues = data.get("issues", [])
    if not issues:
        print("No issues found.")
        return
    for issue in issues:
        issue_fields = issue.get("fields", {})
        key = issue.get("key", "")
        status = (issue_fields.get("status") or {}).get("name", "")
        assignee = (issue_fields.get("assignee") or {}).get("displayName", "Unassigned")
        summary = str(issue_fields.get("summary", "")).replace("\n", " ").strip()
        print(f"{key}\t{status}\t{assignee}\t{summary}")
    total = data.get("total")
    if isinstance(total, int):
        print(f"\nCount: {len(issues)} / {total}")


def _handle_get(args: argparse.Namespace, client: JiraClient) -> None:
    data = client.get_issue(args.key)
    if args.json:
        _print_json(data)
        return
    fields = data.get("fields", {})
    key = data.get("key", args.key)
    summary = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "")
    assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
    reporter = (fields.get("reporter") or {}).get("displayName", "Unknown")
    priority = (fields.get("priority") or {}).get("name", "")
    issue_type = (fields.get("issuetype") or {}).get("name", "")
    description = _adf_to_text(fields.get("description", {})).strip() or "(no description)"
    print(f"{key}: {summary}")
    print(f"Status: {status}")
    print(f"Type: {issue_type}")
    print(f"Priority: {priority}")
    print(f"Assignee: {assignee}")
    print(f"Reporter: {reporter}")
    print("\nDescription:")
    print(description)


def _handle_create(args: argparse.Namespace, client: JiraClient) -> None:
    labels = [item.strip() for item in args.labels.split(",") if item.strip()] if args.labels else None
    data = client.create_issue(
        project=args.project,
        summary=args.summary,
        issue_type=args.issue_type,
        description=args.description,
        labels=labels,
        assignee=args.assignee,
    )
    if args.json:
        _print_json(data)
        return
    key = data.get("key", "(unknown)")
    print(f"Created issue: {key}")
    print(f"Browse: {client.base_url}/browse/{key}")


def _handle_comment(args: argparse.Namespace, client: JiraClient) -> None:
    data = client.add_comment(args.key, args.body)
    if args.json:
        _print_json(data)
        return
    comment_id = data.get("id", "(unknown)")
    print(f"Added comment to {args.key} (id: {comment_id}).")


def _handle_assign(args: argparse.Namespace, client: JiraClient) -> None:
    data = client.assign_issue(args.key, args.account_id)
    if args.json:
        _print_json(data)
        return
    print(f"Assigned {args.key} to account ID {args.account_id}.")


def _handle_transitions(args: argparse.Namespace, client: JiraClient) -> None:
    data = client.list_transitions(args.key)
    if args.json:
        _print_json(data)
        return
    transitions = data.get("transitions", [])
    if not transitions:
        print("No transitions available.")
        return
    for transition in transitions:
        transition_id = transition.get("id", "")
        name = transition.get("name", "")
        to_status = (transition.get("to") or {}).get("name", "")
        print(f"{transition_id}\t{name}\t{to_status}")


def _handle_transition(args: argparse.Namespace, client: JiraClient) -> None:
    client.transition_issue(args.key, args.transition_id)
    if args.comment:
        client.add_comment(args.key, args.comment)
    if args.json:
        payload: Dict[str, Any] = {
            "key": args.key,
            "transition_id": str(args.transition_id),
            "comment_added": bool(args.comment),
        }
        _print_json(payload)
        return
    print(f"Transitioned {args.key} using transition ID {args.transition_id}.")
    if args.comment:
        print("Added transition comment.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jira Cloud helper CLI")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    whoami_parser = subparsers.add_parser("whoami", help="Show authenticated Jira user.")
    whoami_parser.set_defaults(func=_handle_whoami)

    projects_parser = subparsers.add_parser("projects", help="List visible Jira projects.")
    projects_parser.add_argument("--max-results", type=int, default=50)
    projects_parser.set_defaults(func=_handle_projects)

    search_parser = subparsers.add_parser("search", help="Search issues using JQL.")
    search_parser.add_argument("--jql", required=True, help="JQL expression.")
    search_parser.add_argument("--max-results", type=int, default=20)
    search_parser.add_argument(
        "--fields",
        default="summary,status,assignee,priority,issuetype",
        help="Comma-separated list of fields to fetch.",
    )
    search_parser.set_defaults(func=_handle_search)

    get_parser = subparsers.add_parser("get", help="Get details for one issue.")
    get_parser.add_argument("--key", required=True, help="Issue key (example: ENG-123).")
    get_parser.set_defaults(func=_handle_get)

    create_parser = subparsers.add_parser("create", help="Create a Jira issue.")
    create_parser.add_argument("--project", required=True, help="Project key.")
    create_parser.add_argument("--summary", required=True, help="Issue summary.")
    create_parser.add_argument("--description", help="Issue description text.")
    create_parser.add_argument("--issue-type", default="Task", help="Issue type name.")
    create_parser.add_argument("--labels", help="Comma-separated labels.")
    create_parser.add_argument("--assignee", help="Assignee account ID.")
    create_parser.set_defaults(func=_handle_create)

    comment_parser = subparsers.add_parser("comment", help="Add comment to issue.")
    comment_parser.add_argument("--key", required=True, help="Issue key.")
    comment_parser.add_argument("--body", required=True, help="Comment text.")
    comment_parser.set_defaults(func=_handle_comment)

    assign_parser = subparsers.add_parser("assign", help="Assign issue by account ID.")
    assign_parser.add_argument("--key", required=True, help="Issue key.")
    assign_parser.add_argument("--account-id", required=True, help="Atlassian account ID.")
    assign_parser.set_defaults(func=_handle_assign)

    transitions_parser = subparsers.add_parser(
        "transitions",
        help="List available transitions for an issue.",
    )
    transitions_parser.add_argument("--key", required=True, help="Issue key.")
    transitions_parser.set_defaults(func=_handle_transitions)

    transition_parser = subparsers.add_parser("transition", help="Transition issue status.")
    transition_parser.add_argument("--key", required=True, help="Issue key.")
    transition_parser.add_argument("--transition-id", required=True, help="Transition ID.")
    transition_parser.add_argument("--comment", help="Optional comment to add after transition.")
    transition_parser.set_defaults(func=_handle_transition)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        client = JiraClient.from_env()
        args.func(args, client)
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
