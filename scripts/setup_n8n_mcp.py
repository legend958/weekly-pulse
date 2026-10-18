"""Provision the n8n side of Stage G through the n8n public API.

What it does, in order:

  1. ``GET  /api/v1/workflows``    - look for a workflow with the target name
  2. ``POST /api/v1/credentials``  - create the generic Bearer Auth credential
                                     (``httpBearerAuth``) with a known ID, or
                                     ``PATCH`` it if it already exists
  3. ``POST /api/v1/workflows``    - create the MCP workflow with that credential
     (or ``PATCH`` the existing one)
  4. ``POST /api/v1/workflows/{id}/publish`` - publish it so the Production URL
     answers

It never prints or transmits any secret except to your own n8n instance, and it
reads secrets from ``.env.local`` so they need not touch the command line.

Usage
-----
  # 1. sanity check the URL + API key (read-only)
  python scripts/setup_n8n_mcp.py --check

  # 2. show exactly what would be sent, send nothing
  python scripts/setup_n8n_mcp.py --dry-run

  # 3. do it
  python scripts/setup_n8n_mcp.py

Configuration (precedence: CLI flag > environment > .env.local)
---------------------------------------------------------------
  PULSE_N8N_BASE_URL      e.g. https://myorg.app.n8n.cloud  (or http://localhost:5678)
  PULSE_N8N_API_KEY       n8n API key, needs scopes credential:create,
                          workflow:create, workflow:activate
  PULSE_MCP_TOKEN         the shared bearer secret (generated for you already)

Optional:
  --credential-id         ID to give the Bearer credential (<=16 chars)
  --credential-name       display name for the Bearer credential
  --workflow-name         name of the workflow to create/find
  --note-target           Google Doc ID/URL for append_to_note
  --google-docs-credential-id / --gmail-credential-id
  --no-credential         skip step 2 and use --credential-id as-is (create the
                          credential by hand in the UI, e.g. on older n8n)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env.local")
WORKFLOW_SOURCE = os.path.join(ROOT, "docs", "n8n-weekly-pulse-mcp.json")

DEFAULT_WORKFLOW_NAME = "Weekly Product Pulse - MCP publish"
DEFAULT_CRED_ID = "pulseMcpBearer1"
DEFAULT_CRED_NAME = "Weekly Pulse MCP (Bearer)"
DEFAULT_DOC_TARGET = ""  # empty keeps whatever the source file has
DEFAULT_GDOCS_CRED_ID = "77YPwrdD4clfMUnS"
DEFAULT_GDOCS_CRED_NAME = "Google Docs account"
DEFAULT_GMAIL_CRED_ID = "wPxafhsftdettiAZ"
DEFAULT_GMAIL_CRED_NAME = "Gmail account"
MCP_PATH = "weekly-pulse"
REQUIRED_SCOPES = "credential:create, workflow:create, workflow:activate"


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def load_env_file(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    if not os.path.exists(path):
        return data
    with open(path, encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def resolve(cli_value, env_key: str, env_file: dict[str, str], default=None):
    if cli_value not in (None, ""):
        return cli_value
    if os.environ.get(env_key):
        return os.environ[env_key]
    if env_file.get(env_key):
        return env_file[env_key]
    return default


def set_env_file_value(path: str, key: str, value: str) -> None:
    """Set KEY=value in .env.local, preserving the rest of the file."""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip("\n") + "\n")


def normalise_base_url(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    return url


# --------------------------------------------------------------------------- #
# tiny HTTP client
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    def __init__(self, status: int, body: str, method: str, path: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body
        self.method = method
        self.path = path


class Api:
    def __init__(self, base_url: str, api_key: str, dry_run: bool = False):
        self.base_url = base_url
        self.api_key = api_key
        self.dry_run = dry_run

    def _request(self, method: str, path: str, payload=None):
        url = f"{self.base_url}/api/v1{path}"
        data = None
        headers = {"Accept": "application/json", "X-N8N-API-KEY": self.api_key}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise ApiError(exc.code, body, method, path) from None
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"\nCannot reach {url}\n  {exc.reason}\n"
                "Check PULSE_N8N_BASE_URL (include https:// and no /api/v1)."
            ) from None

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload):
        return self._request("POST", path, payload)

    def patch(self, path: str, payload):
        return self._request("PATCH", path, payload)

    def get_all(self, path: str) -> list[dict]:
        """Follow n8n's cursor pagination and return every row."""
        rows: list[dict] = []
        cursor = None
        while True:
            sep = "&" if "?" in path else "?"
            url = f"{path}{sep}limit=250" + (f"&cursor={cursor}" if cursor else "")
            _, body = self.get(url)
            rows.extend(body.get("data", []))
            cursor = body.get("nextCursor")
            if not cursor:
                return rows


# --------------------------------------------------------------------------- #
# workflow payload
# --------------------------------------------------------------------------- #
def build_workflow_payload(cred_id: str, cred_name: str, args) -> dict:
    with open(WORKFLOW_SOURCE, encoding="utf-8") as handle:
        source = json.load(handle)

    nodes = json.loads(json.dumps(source["nodes"]))  # deep copy
    for node in nodes:
        name = node.get("name")
        if name == "MCP Server Trigger":
            node["parameters"]["authentication"] = "bearerAuth"
            node["parameters"]["path"] = MCP_PATH
            node["credentials"] = {
                "httpBearerAuth": {"id": cred_id, "name": cred_name}
            }
        elif name == "append_to_note":
            if args.note_target:
                node["parameters"]["documentURL"] = args.note_target
            node["credentials"] = {
                "googleDocsOAuth2Api": {
                    "id": args.google_docs_credential_id,
                    "name": DEFAULT_GDOCS_CRED_NAME,
                }
            }
        elif name == "create_email_draft":
            node["credentials"] = {
                "gmailOAuth2": {
                    "id": args.gmail_credential_id,
                    "name": DEFAULT_GMAIL_CRED_NAME,
                }
            }
        elif node.get("type") == "n8n-nodes-base.stickyNote":
            node["parameters"]["content"] = (
                "## Weekly Product Pulse - MCP publish\n\n"
                "Provisioned by `scripts/setup_n8n_mcp.py`.\n\n"
                f"- **Bearer credential**: `{cred_name}` (`{cred_id}`)\n"
                f"- **Path**: `{MCP_PATH}` -> Production URL `/mcp/{MCP_PATH}`\n"
                "- **Tools**: `append_to_note`, `create_email_draft` (do not rename)\n\n"
                "The pipeline authenticates with `Authorization: Bearer <PULSE_MCP_TOKEN>`."
            )

    # Only fields the public API accepts (active/tags are read-only).
    return {
        "name": args.workflow_name,
        "nodes": nodes,
        "connections": source["connections"],
        "settings": source.get("settings", {"executionOrder": "v1"}),
        "pinData": {},
    }


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def find_workflow(api: Api, name: str):
    for wf in api.get_all("/workflows"):
        if wf.get("name") == name:
            return wf
    return None


def ensure_credential(api: Api, args, token: str) -> tuple[str, str]:
    """Create (or repair) the Bearer credential; return (id, name)."""
    existing = None
    try:
        for cred in api.get_all("/credentials"):
            if cred.get("id") == args.credential_id:
                existing = cred
                break
    except ApiError as exc:
        if exc.status == 403:
            print("  ! cannot list credentials (scope credential:list missing); "
                  "will try to create anyway")
        else:
            raise

    if existing is None:
        try:
            _, body = api.post(
                "/credentials",
                {
                    "id": args.credential_id,
                    "name": args.credential_name,
                    "type": "httpBearerAuth",
                    "data": {"token": token},
                },
            )
            print(f"  + created credential {args.credential_name!r} "
                  f"(id {body.get('id', args.credential_id)})")
            return args.credential_id, args.credential_name
        except ApiError as exc:
            if exc.status in (404, 405):
                raise SystemExit(
                    "\nThis n8n version does not expose POST /credentials.\n"
                    "Create the credential by hand (Credentials -> New -> 'Bearer "
                    "Auth', paste PULSE_MCP_TOKEN), note its ID, then re-run with\n"
                    f"  --no-credential --credential-id <that-id>\n\n"
                    f"API said: HTTP {exc.status} {exc.body[:200]}"
                ) from None
            if exc.status == 409:
                existing = {"id": args.credential_id, "name": args.credential_name}
            else:
                raise

    # Already present -> make sure the stored token is the one we expect.
    try:
        api.patch(f"/credentials/{args.credential_id}", {"data": {"token": token}})
        print(f"  ~ updated credential token for {args.credential_name!r} "
              f"(id {args.credential_id})")
    except ApiError as exc:
        print(f"  ! could not update existing credential (HTTP {exc.status}); "
              "reusing it unchanged")
    return args.credential_id, args.credential_name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Provision the n8n MCP workflow.")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--token")
    parser.add_argument("--credential-id", default=None)
    parser.add_argument("--credential-name", default=None)
    parser.add_argument("--workflow-name", default=None)
    parser.add_argument("--note-target", default=None)
    parser.add_argument("--google-docs-credential-id", default=None)
    parser.add_argument("--gmail-credential-id", default=None)
    parser.add_argument("--no-credential", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="read-only: verify URL + API key")
    parser.add_argument("--dry-run", action="store_true",
                        help="print requests, send nothing")
    args = parser.parse_args(argv)

    env_file = load_env_file(ENV_FILE)
    base_url = resolve(args.base_url, "PULSE_N8N_BASE_URL", env_file)
    api_key = resolve(args.api_key, "PULSE_N8N_API_KEY", env_file)
    token = resolve(args.token, "PULSE_MCP_TOKEN", env_file)

    args.credential_id = resolve(args.credential_id, "PULSE_MCP_CREDENTIAL_ID",
                                 env_file, DEFAULT_CRED_ID)
    args.credential_name = resolve(args.credential_name, "PULSE_MCP_CREDENTIAL_NAME",
                                   env_file, DEFAULT_CRED_NAME)
    args.workflow_name = resolve(args.workflow_name, "PULSE_N8N_WORKFLOW_NAME",
                                 env_file, DEFAULT_WORKFLOW_NAME)
    args.note_target = resolve(args.note_target, "PULSE_MCP_NOTE_TARGET",
                               env_file, DEFAULT_DOC_TARGET)
    args.google_docs_credential_id = resolve(
        args.google_docs_credential_id, "PULSE_GDOCS_CREDENTIAL_ID",
        env_file, DEFAULT_GDOCS_CRED_ID)
    args.gmail_credential_id = resolve(
        args.gmail_credential_id, "PULSE_GMAIL_CREDENTIAL_ID",
        env_file, DEFAULT_GMAIL_CRED_ID)

    missing = [n for n, v in (("PULSE_N8N_BASE_URL", base_url),
                              ("PULSE_N8N_API_KEY", api_key)) if not v]
    if missing:
        print("Missing required configuration: " + ", ".join(missing))
        print(f"\nPut them in {ENV_FILE}:")
        print("  PULSE_N8N_BASE_URL=https://your-instance.app.n8n.cloud")
        print("  PULSE_N8N_API_KEY=<key with scopes: " + REQUIRED_SCOPES + ">")
        return 2

    base_url = normalise_base_url(base_url)
    if not token:
        print("Missing PULSE_MCP_TOKEN (the shared bearer secret).")
        return 2

    if len(args.credential_id) > 16:
        print(f"--credential-id must be <= 16 chars, got {len(args.credential_id)}")
        return 2

    api = Api(base_url, api_key)
    payload = build_workflow_payload(args.credential_id, args.credential_name, args)

    print(f"n8n instance : {base_url}")
    print(f"workflow     : {args.workflow_name!r}")
    print(f"credential   : {args.credential_name!r} (id {args.credential_id})")
    print(f"doc target   : {args.note_target or '(kept from source file)'}")
    print(f"token        : {'*' * 8}{token[-4:]} ({len(token)} chars)")
    print()

    # -- dry run ---------------------------------------------------------- #
    if args.dry_run:
        print("DRY RUN - nothing sent.\n")
        print(f"POST /api/v1/credentials\n{json.dumps({'id': args.credential_id, 'name': args.credential_name, 'type': 'httpBearerAuth', 'data': {'token': '<PULSE_MCP_TOKEN>'}}, indent=2)}\n")
        print(f"POST /api/v1/workflows\n{json.dumps(payload, indent=2)}")
        print(f"\nPOST /api/v1/workflows/<id>/publish")
        return 0

    # -- check ------------------------------------------------------------ #
    if args.check:
        try:
            _, body = api.get("/workflows?limit=1")
        except ApiError as exc:
            print(f"FAILED: HTTP {exc.status}")
            print(f"  {exc.body[:300]}")
            if exc.status == 401:
                print("  -> API key rejected. Check PULSE_N8N_API_KEY.")
            if exc.status == 403:
                print("  -> Key lacks scopes. Need: " + REQUIRED_SCOPES)
            return 1
        print("OK: URL and API key work. ")
        print(f"  instance reports {len(body.get('data', []))} workflow(s) in first page")
        print("  (scopes are enforced per-endpoint; run without --check to provision)")
        return 0

    # -- 1. workflow lookup ----------------------------------------------- #
    existing = find_workflow(api, args.workflow_name)
    if existing:
        print(f"  = workflow already exists (id {existing.get('id')}); will update")
    else:
        print("  - workflow not found; will create")

    # -- 2. credential ---------------------------------------------------- #
    if args.no_credential:
        print(f"  = --no-credential: using existing credential id "
              f"{args.credential_id}")
    else:
        ensure_credential(api, args, token)

    # -- verify the Google/Gmail credentials exist ------------------------- #
    try:
        ids = {c.get("id") for c in api.get_all("/credentials")}
        for label, cid in (("Google Docs", args.google_docs_credential_id),
                           ("Gmail", args.gmail_credential_id)):
            if cid in ids:
                print(f"  = {label} credential {cid} found")
            else:
                print(f"  ! {label} credential {cid} NOT found on this instance - "
                      "attach it in the UI before running the pipeline")
    except ApiError:
        print("  ! could not list credentials to verify the Google/Gmail ones")

    # -- 3. create / update ----------------------------------------------- #
    if existing:
        wf_id = existing["id"]
        api.patch(f"/workflows/{wf_id}", payload)
        print(f"  ~ workflow updated (id {wf_id})")
    else:
        _, created = api.post("/workflows", payload)
        wf_id = created["id"]
        print(f"  + workflow created (id {wf_id})")

    # -- 4. publish -------------------------------------------------------- #
    api.post(f"/workflows/{wf_id}/publish", {})
    print("  + workflow published")

    endpoint = f"{base_url}/mcp/{MCP_PATH}"
    set_env_file_value(ENV_FILE, "PULSE_MCP_ENDPOINT", endpoint)
    set_env_file_value(ENV_FILE, "PULSE_MCP_TRANSPORT", "http")
    print(f"  + wrote PULSE_MCP_ENDPOINT to {os.path.basename(ENV_FILE)}")

    print(f"\nProduction MCP URL: {endpoint}")
    print("\nNext:")
    print("  python scripts/check_n8n_endpoint.py")
    print("  python run_pipeline.py --decision approve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
