#!/usr/bin/env python3
"""
permishifizer9000.py

Generates the SalesforceBackup permission set XML from metadata in a
connected Salesforce org.  See README.md for full documentation.
"""

import argparse
import json
import locale
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

PERMSET_NAME = "SalesforceBackup"
PERMSET_LABEL = "Salesforce Backup"
PERMSET_DESC = (
    "Grants object, field, and record type access for full-record backup/restore "
    "scope (excluding ChangeEvent and __e event objects)."
)
DEFAULT_PERMSET_FILE = (
    Path(__file__).resolve().parent
    / "force-app"
    / "main"
    / "default"
    / "permissionsets"
    / f"{PERMSET_NAME}.permissionset-meta.xml"
)

XMLNS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", XMLNS)

BATCH_SIZE = 50
QUERY_PAGE_SIZE = 2000

EXCLUDED_OBJECT_API_NAMES = set()

OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS = {"Task", "Event"}

FIELD_QUERY_SUPPLEMENTAL_OBJECTS = [
    "EmailMessage",
    "User",
]

# Fully-qualified field API names (Object.Field) that are valid in
# fieldPermissions but are not returned by EntityParticle or FieldDefinition
# queries. They are still permissionable through the Salesforce permission
# set UI. Each entry is injected verbatim into fieldPermissions provided
# its parent object is not excluded.
FIELD_PERMISSION_SUPPLEMENTAL_FIELDS = {
    "DisputeItemChargeback.CardBrand",
    "Idea.Attachment",
}

USER_PERMISSION_NAMES = {
    "AllowViewEditConvertedLeads",
    "AssignTopics",
    "CanAccessCE",
    "ConnectOrgToEnvironmentHub",
    "ConvertLeads",
    "CreateCustomizeDashboards",
    "CreateCustomizeFilters",
    "CreateCustomizeReports",
    "CreateDashboardFolders",
    "CreateReportFolders",
    "CreateTopics",
    "CustomizeApplication",
    "DeleteTopics",
    "EditEvent",
    "EditMyDashboards",
    "EditMyReports",
    "EditPublicDocuments",
    "EditPublicFilters",
    "EditPublicTemplates",
    "EditReadonlyFields",
    "EditTask",
    "EditTopics",
    "ImportLeads",
    "ManageCategories",
    "ManageCustomPermissions",
    "ManageDashbdsInPubFolders",
    "ManageNetworks",
    "ManagePvtRptsAndDashbds",
    "ManageQuotas",
    "ManageReportsInPubFolders",
    "ManageTranslation",
    "ModifyAllData",
    "ModifyMetadata",
    "OverrideForecasts",
    "QueryAllFiles",
    "RunReports",
    "SolutionImport",
    "TransferAnyEntity",
    "TransferAnyLead",
    "UseTeamReassignWizards",
    "ViewAllCustomSettings",
    "ViewAllData",
    "ViewAllForecasts",
    "ViewAllForeignKeyNames",
    "ViewDataLeakageEvents",
    "ViewEncryptedData",
    "ViewEventLogFiles",
    "ViewPlatformEvents",
    "ViewPublicDashboards",
    "ViewPublicReports",
    "ViewRoles",
    "ViewSetup",
}

@dataclass
class OrgContext:
    """Holds per-run Salesforce connection state instead of module globals."""
    access_token: str
    instance_url: str
    api_version: str

    @property
    def rest_base(self) -> str:
        return f"{self.instance_url}/services/data/v{self.api_version}"

    @property
    def tooling_base(self) -> str:
        return f"{self.rest_base}/tooling"


MIN_PYTHON_VERSION = (3, 12)
HTTP_TIMEOUT_SECONDS = 90
SF_COMMAND_TIMEOUT_SECONDS = 120
DEPLOY_TIMEOUT_BUFFER_SECONDS = 300
TARGET_ORG_ENV_VAR = "SF_TARGET_ORG"


class ScriptError(Exception):
    pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate the SalesforceBackup permission set from org metadata."
    )
    parser.add_argument(
        "target_org_positional",
        nargs="?",
        metavar="target_org",
        help="Optional Salesforce org alias/username (convenience shorthand).",
    )
    parser.add_argument(
        "--target-org",
        "-o",
        dest="target_org",
        help="Salesforce org alias/username (preferred for CI/CD; same meaning as positional target_org).",
    )
    parser.add_argument(
        "--output-file",
        "-f",
        type=Path,
        default=DEFAULT_PERMSET_FILE,
        help=(
            "Output permission set XML path. "
            f"Default: {DEFAULT_PERMSET_FILE}"
        ),
    )
    parser.add_argument(
        "--deploy",
        "-d",
        action="store_true",
        help="Automatically deploy the generated permission set after writing it.",
    )
    parser.add_argument(
        "--deploy-wait",
        "-w",
        type=int,
        default=10,
        help="Minutes to wait for deployment completion when using --deploy (default: 10). Must be >= 1.",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Emit a machine-readable JSON summary to stdout; progress is sent to stderr.",
    )
    parser.add_argument(
        "--check-only",
        "-c",
        action="store_true",
        help="Validate the generated permission set against the org (dry-run) without deploying it.",
    )
    return parser.parse_args()


def resolve_target_org(args):
    positional = (args.target_org_positional or "").strip()
    named = (args.target_org or "").strip()

    if positional and named and positional != named:
        die(
            "Error: Conflicting target org values were provided.\n"
            f"  positional target_org: {positional}\n"
            f"  --target-org: {named}"
        )

    target_org = named or positional
    if target_org:
        return target_org

    env_target_org = (os.environ.get(TARGET_ORG_ENV_VAR, "") or "").strip()
    return env_target_org or None


def qtag(name):
    return f"{{{XMLNS}}}{name}"


def local_name(elem):
    t = elem.tag
    return t.split("}", 1)[1] if "}" in t else t


def die(message):
    raise ScriptError(message)


def decode_subprocess_output(raw_data):
    if raw_data is None:
        return ""
    if isinstance(raw_data, str):
        return raw_data

    preferred_encoding = (locale.getpreferredencoding(False) or "").strip()
    encodings_to_try = ["utf-8"]
    if preferred_encoding and preferred_encoding.lower() != "utf-8":
        encodings_to_try.append(preferred_encoding)

    for encoding in encodings_to_try:
        try:
            return raw_data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw_data.decode("utf-8", errors="replace")


def ensure_python_version():
    current = sys.version_info[:3]
    if current < MIN_PYTHON_VERSION:
        required = ".".join(str(part) for part in MIN_PYTHON_VERSION)
        actual = ".".join(str(part) for part in current)
        die(f"Error: Python {required}+ is required. Found Python {actual}.")


def find_project_root_with_sfdx(start_dir):
    current = start_dir.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "sfdx-project.json").is_file():
            return candidate
    return None


def load_package_directories(project_root):
    project_file = project_root / "sfdx-project.json"
    try:
        with project_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        die(f"Error: Could not read `{project_file}`: {exc}")
    except json.JSONDecodeError as exc:
        die(f"Error: Could not parse `{project_file}` as JSON: {exc}")

    package_dirs = data.get("packageDirectories")
    if not isinstance(package_dirs, list) or not package_dirs:
        die(
            "Error: `sfdx-project.json` must contain a non-empty "
            "`packageDirectories` array when using --deploy."
        )

    resolved_dirs = []
    for entry in package_dirs:
        if not isinstance(entry, dict):
            continue
        rel_path = (entry.get("path") or "").strip()
        if not rel_path:
            continue
        resolved_dirs.append((project_root / rel_path).resolve())

    if not resolved_dirs:
        die(
            "Error: No valid `packageDirectories[].path` values found in "
            "`sfdx-project.json` when using --deploy."
        )

    return project_file, resolved_dirs


def validate_deploy_preconditions(output_file):
    project_root = (
        find_project_root_with_sfdx(output_file.parent)
        or find_project_root_with_sfdx(Path.cwd())
        or find_project_root_with_sfdx(Path(__file__).resolve().parent)
    )
    if project_root is None:
        die(
            "Error: --deploy requires `sfdx-project.json` at the project root.\n"
            "Could not locate one from output path, current working directory, or script directory."
        )

    project_file, package_dirs = load_package_directories(project_root)

    if not any(output_file.is_relative_to(pkg_dir) for pkg_dir in package_dirs):
        package_dir_list = ", ".join(str(path) for path in package_dirs)
        die(
            "Error: --deploy requires the generated metadata file to be within a configured package directory.\n"
            f"  Output file: {output_file}\n"
            f"  Project file: {project_file}\n"
            f"  packageDirectories: {package_dir_list}"
        )


def sf_resolution_diagnostics():
    candidates = ["sf", "sf.cmd", "sf.exe", "sf.bat"]
    found = []
    missing = []
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            found.append(f"{candidate}={path}")
        else:
            missing.append(candidate)

    bash_path = shutil.which("bash")
    bash_value = bash_path if bash_path else "not found"

    lines = []
    if found:
        lines.append("Found command candidates: " + ", ".join(found))
    if missing:
        lines.append("Missing command candidates: " + ", ".join(missing))
    lines.append(f"bash lookup: {bash_value}")
    return "\n".join(lines)


def resolve_sf_invoker():
    direct_candidates = ["sf", "sf.cmd", "sf.exe", "sf.bat"]
    for candidate in direct_candidates:
        sf_path = shutil.which(candidate)
        if not sf_path:
            continue
        try:
            probe = subprocess.run(
                [sf_path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                check=False,
                timeout=SF_COMMAND_TIMEOUT_SECONDS,
            )
            if probe.returncode == 0:
                return ("direct", sf_path)
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            continue

    bash_path = shutil.which("bash")
    if os.name == "nt" and bash_path:
        try:
            probe = subprocess.run(
                [bash_path, "-lc", "sf --version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                check=False,
                timeout=SF_COMMAND_TIMEOUT_SECONDS,
            )
            if probe.returncode == 0:
                return ("bash", bash_path)
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass

    return None


def build_sf_command(sf_invoker, sf_args):
    mode, executable = sf_invoker
    if mode == "direct":
        return [executable] + sf_args
    joined = "sf " + " ".join(shlex.quote(part) for part in sf_args)
    return [executable, "-lc", joined]


def run_sf_org_display(target_org, sf_invoker):
    sf_args = ["org", "display", "--json"]
    if target_org:
        sf_args.extend(["--target-org", target_org])

    cmd = build_sf_command(sf_invoker, sf_args)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
            timeout=SF_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        die(
            "Error: Salesforce CLI command timed out.\n"
            f"  Command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"  Timeout: {SF_COMMAND_TIMEOUT_SECONDS}s"
        )
    except OSError as exc:
        die(f"Error: Failed to invoke Salesforce CLI: {exc}")

    if proc.returncode != 0:
        stderr = decode_subprocess_output(proc.stderr).strip()
        stdout = decode_subprocess_output(proc.stdout).strip()
        details = stderr or stdout or "No additional output from Salesforce CLI."
        die(
            "Error: `sf org display --json` failed.\n"
            f"  Command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"  Details: {details}"
        )

    if proc.stderr:
        sys.stderr.write(decode_subprocess_output(proc.stderr))

    return decode_subprocess_output(proc.stdout)


def run_sf_org_auth_show_access_token(target_org, sf_invoker):
    sf_args = ["org", "auth", "show-access-token", "--json"]
    if target_org:
        sf_args.extend(["--target-org", target_org])

    cmd = build_sf_command(sf_invoker, sf_args)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
            timeout=SF_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        die(
            "Error: Salesforce CLI command timed out.\n"
            f"  Command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"  Timeout: {SF_COMMAND_TIMEOUT_SECONDS}s"
        )
    except OSError as exc:
        die(f"Error: Failed to invoke Salesforce CLI: {exc}")

    if proc.returncode != 0:
        stderr = decode_subprocess_output(proc.stderr).strip()
        stdout = decode_subprocess_output(proc.stdout).strip()
        details = stderr or stdout or "No additional output from Salesforce CLI."
        die(
            "Error: `sf org auth show-access-token --json` failed.\n"
            f"  Command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"  Details: {details}\n"
            "  Hint: This command requires Salesforce CLI with the `show-access-token` topic.\n"
            "  Update the CLI: `sf update` (or reinstall from https://developer.salesforce.com/tools/salesforcecli)."
        )

    # Note: this command emits a sensitive-token warning to stderr by design.
    # Do not forward it to the user's stderr to avoid leaking the warning verbatim
    # in logs; the JSON result is what we care about.

    return decode_subprocess_output(proc.stdout)


# Matches C0/C1 control characters and ANSI escape sequences (e.g. CLI spinner
# output) so a stderr stream containing only such noise can be treated as empty.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\x1b\[[0-9;]*[A-Za-z]")

# Benign informational lines the Salesforce CLI writes to stderr that are
# unrelated to a deploy/validation failure (e.g. "update available" notices).
_BENIGN_STDERR_LINE = re.compile(r"warning:.*\bupdate available\b", re.IGNORECASE)


def clean_cli_stderr(text):
    """Strip ANSI/control noise and drop benign CLI warnings from a stderr stream."""
    text = _CONTROL_CHARS.sub("", text)
    kept = []
    for line in text.splitlines():
        stripped = line.strip(" \t\r\u00bb\u203a\u276f\u2022").strip()
        if not stripped or _BENIGN_STDERR_LINE.search(stripped):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def run_sf_project_deploy(output_file, target_org, sf_invoker, wait_minutes, check_only=False):
    sf_args = [
        "project",
        "deploy",
        "start",
        "--ignore-conflicts",
        "--source-dir",
        str(output_file),
        "--wait",
        str(wait_minutes),
    ]
    if check_only:
        sf_args.append("--dry-run")
    if target_org:
        sf_args.extend(["--target-org", target_org])

    cmd = build_sf_command(sf_invoker, sf_args)
    action = "validation" if check_only else "deployment"
    timeout_seconds = max(
        SF_COMMAND_TIMEOUT_SECONDS,
        (wait_minutes * 60) + DEPLOY_TIMEOUT_BUFFER_SECONDS,
    )

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        die(
            f"Error: Salesforce {action} command timed out.\n"
            f"  Command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"  Timeout: {timeout_seconds}s"
        )
    except OSError as exc:
        die(f"Error: Failed to invoke Salesforce CLI for deployment: {exc}")

    if proc.returncode != 0:
        stderr = decode_subprocess_output(proc.stderr).strip()
        stdout = decode_subprocess_output(proc.stdout).strip()
        lowered = f"{stdout}\n{stderr}".lower()
        hint = ""
        if (
            "sfdx-project.json" in lowered
            or "packagedirectories" in lowered
            or "package directory" in lowered
        ):
            hint = (
                "\n  Hint: Ensure `sfdx-project.json` exists at project root and "
                "the output file is under a configured `packageDirectories[].path`."
            )
        detail_sections = []
        if stdout:
            detail_sections.append(f"  --- Salesforce CLI output ---\n{stdout}")
        stderr_clean = clean_cli_stderr(stderr)
        if stderr_clean:
            detail_sections.append(f"  --- Salesforce CLI errors ---\n{stderr_clean}")
        if not detail_sections:
            detail_sections.append("  No additional output from Salesforce CLI.")
        details_block = "\n".join(detail_sections)
        die(
            f"Error: Salesforce {action} failed "
            f"(`sf project deploy start`, exit code {proc.returncode}).\n"
            f"  Command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"{details_block}"
            f"{hint}"
        )

    if proc.stderr:
        sys.stderr.write(decode_subprocess_output(proc.stderr))
    if proc.stdout:
        print(decode_subprocess_output(proc.stdout))


def http_get_json(url, context, org):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {org.access_token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            payload = resp.read()
            try:
                return json.loads(payload)
            except json.JSONDecodeError as exc:
                die(f"Error: Invalid JSON response during {context}: {exc}")
    except socket.timeout:
        die(f"Error: Timeout after {HTTP_TIMEOUT_SECONDS}s during {context}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        die(f"Error: HTTP {exc.code} during {context}\n  {body[:400]}")
    except urllib.error.URLError as exc:
        reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
        lowered = reason.lower()
        if "timed out" in lowered or "timeout" in lowered:
            die(f"Error: Timeout after {HTTP_TIMEOUT_SECONDS}s during {context}: {reason}")
        die(f"Error: Network failure during {context}: {reason}")


def _query(base_url, soql, org, *, label):
    """Shared query helper for REST and Tooling API endpoints."""
    context = f"{label} query: {soql[:120]}..."
    url = f"{base_url}/query/?q={urllib.parse.quote(soql)}"
    records = []
    while url:
        data = http_get_json(url, context=context, org=org)
        records.extend(data.get("records", []))
        if data.get("done", True):
            break
        next_url = data.get("nextRecordsUrl", "")
        url = f"{org.instance_url}{next_url}" if next_url else None
    return records


def rest_query(soql, org):
    return _query(org.rest_base, soql, org, label="REST")


def tooling_query(soql, org):
    return _query(org.tooling_base, soql, org, label="Tooling")


def metadata_list_recordtype_fullnames(org):
    endpoint = f"{org.instance_url}/services/Soap/m/{org.api_version}"
    session_id = (
        org.access_token.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/" xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <env:Header>
    <met:SessionHeader>
      <met:sessionId>{session_id}</met:sessionId>
    </met:SessionHeader>
  </env:Header>
  <env:Body>
    <met:listMetadata>
      <met:queries>
        <met:type>RecordType</met:type>
      </met:queries>
      <met:asOfVersion>{org.api_version}</met:asOfVersion>
    </met:listMetadata>
  </env:Body>
</env:Envelope>"""

    req = urllib.request.Request(
        endpoint,
        data=envelope.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=UTF-8",
            "SOAPAction": "listMetadata",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            response_xml = resp.read()
    except socket.timeout:
        die(
            "Error: Timeout during Metadata API listMetadata request.\n"
            f"  Timeout: {HTTP_TIMEOUT_SECONDS}s"
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        die(f"Error: Metadata API listMetadata request failed.\n  HTTP {exc.code}: {body[:400]}")
    except urllib.error.URLError as exc:
        die(f"Error: Network failure during Metadata API listMetadata: {exc}")
    except OSError as exc:
        die(f"Error: Could not execute Metadata API request: {exc}")

    try:
        root = ET.fromstring(response_xml)
    except ET.ParseError as exc:
        die(f"Error: Could not parse Metadata API response: {exc}")

    ns = {
        "env": "http://schemas.xmlsoap.org/soap/envelope/",
        "met": "http://soap.sforce.com/2006/04/metadata",
    }

    fault = root.find(".//env:Fault", ns)
    if fault is not None:
        fault_text = ""
        # Try unnamespaced first, then SOAP-namespaced faultstring.
        fault_str = fault.find("faultstring")
        if fault_str is None:
            fault_str = fault.find("{http://schemas.xmlsoap.org/soap/envelope/}faultstring")
        if fault_str is None:
            fault_str = fault.find(".//faultstring")
        if fault_str is not None and fault_str.text:
            fault_text = fault_str.text.strip()
        if fault_text:
            die(
                "Error: Metadata API returned a SOAP fault during listMetadata.\n"
                f"  {fault_text}"
            )
        die("Error: Metadata API returned a SOAP fault during listMetadata.")

    full_names = []
    for elem in root.findall(".//met:listMetadataResponse/met:result/met:fullName", ns):
        value = (elem.text or "").strip()
        if value:
            full_names.append(value)

    return full_names


def is_excluded_event_object(object_api_name):
    lower = (object_api_name or "").lower()
    return lower.endswith("changeevent") or lower.endswith("__e")


_EXCLUDED_OBJECT_API_NAMES_LOWER = {n.lower() for n in EXCLUDED_OBJECT_API_NAMES}


def is_excluded_predefined_object(object_api_name):
    return (object_api_name or "").lower() in _EXCLUDED_OBJECT_API_NAMES_LOWER


def is_excluded_object(object_api_name):
    return is_excluded_event_object(object_api_name) or is_excluded_predefined_object(object_api_name)


def connected_org_display(result, target_org):
    username = (result.get("username") or "").strip()
    alias = (result.get("alias") or "").strip()

    if username and alias:
        return f"{username} (alias: {alias})"
    if username:
        return username
    if alias:
        return f"alias: {alias}"

    if target_org:
        return target_org

    return "default org"


def picklist_where(last_value=None):
    where = (
        "EntityParticle.EntityDefinition.QualifiedApiName = 'ObjectPermissions' "
        "AND EntityParticle.QualifiedApiName = 'SobjectType' "
        "AND IsActive = TRUE"
    )
    if last_value:
        where += f" AND Value > '{last_value}'"
    return where


def picklist_fetch_batch(org, last_value=None):
    soql = (
        "SELECT Value FROM PicklistValueInfo "
        f"WHERE {picklist_where(last_value)} "
        "ORDER BY Value "
        f"LIMIT {QUERY_PAGE_SIZE}"
    )
    recs = rest_query(soql, org)
    return [r.get("Value", "") for r in recs if r.get("Value")]


def fetch_picklist_object_names(org):
    names = []
    last_value = None

    while True:
        batch = picklist_fetch_batch(org, last_value)
        if not batch:
            break

        names.extend(batch)
        if len(batch) < QUERY_PAGE_SIZE:
            break

        last_value = batch[-1]

    return names


def entitydef_where(last_value=None):
    where = "IsDeprecatedAndHidden = FALSE"
    if last_value:
        where += f" AND QualifiedApiName > '{last_value}'"
    return where


def entitydef_fetch_batch(org, last_value=None):
    soql = (
        "SELECT QualifiedApiName, IsFlsEnabled FROM EntityDefinition "
        f"WHERE {entitydef_where(last_value)} "
        "ORDER BY QualifiedApiName "
        f"LIMIT {QUERY_PAGE_SIZE}"
    )
    recs = tooling_query(soql, org)
    return [
        (r.get("QualifiedApiName", ""), bool(r.get("IsFlsEnabled")))
        for r in recs if r.get("QualifiedApiName")
    ]


def fetch_entity_definition_objects(org):
    """Return a list of (QualifiedApiName, IsFlsEnabled) tuples."""
    results = []
    last_value = None

    while True:
        batch = entitydef_fetch_batch(org, last_value)
        if not batch:
            break

        results.extend(batch)
        if len(batch) < QUERY_PAGE_SIZE:
            break

        last_value = batch[-1][0]  # QualifiedApiName of the last tuple

    return results


def sort_key(elem):
    tag_name = local_name(elem)
    if tag_name == "fieldPermissions":
        field_el = elem.find(qtag("field"))
        return (tag_name, field_el.text if field_el is not None and field_el.text else "")
    if tag_name == "objectPermissions":
        obj_el = elem.find(qtag("object"))
        return (tag_name, obj_el.text if obj_el is not None and obj_el.text else "")
    if tag_name == "recordTypeVisibilities":
        rt_el = elem.find(qtag("recordType"))
        return (tag_name, rt_el.text if rt_el is not None and rt_el.text else "")
    if tag_name == "userPermissions":
        name_el = elem.find(qtag("name"))
        return (tag_name, name_el.text if name_el is not None and name_el.text else "")
    return (tag_name, "")


def main():
    ensure_python_version()

    args = parse_args()

    # In --json mode, route human-readable progress to stderr so stdout carries
    # only the final JSON summary.
    original_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr

    output_file = args.output_file.expanduser().resolve()
    target_org = resolve_target_org(args)

    if (args.deploy or args.check_only) and args.deploy_wait < 1:
        die("Error: --deploy-wait must be 1 or greater.")

    if args.deploy and args.check_only:
        die("Error: --deploy and --check-only cannot be used together.")

    if args.deploy or args.check_only:
        validate_deploy_preconditions(output_file)

    sf_invoker = resolve_sf_invoker()
    if sf_invoker is None:
        die(
            "Error: Salesforce CLI (sf) was not runnable from this environment.\n"
            "Install from: https://developer.salesforce.com/tools/salesforcecli\n"
            "Ensure `sf` is on PATH in the same shell where Python is run.\n"
            f"{sf_resolution_diagnostics()}"
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    print("Connecting to Salesforce org...")
    org_info_json = run_sf_org_display(target_org, sf_invoker)
    if not org_info_json.strip():
        die("Error: Salesforce CLI returned empty output for org display.")

    try:
        org_info = json.loads(org_info_json)
    except json.JSONDecodeError:
        die("Error: Could not parse org display output. Are you authenticated?\n  Run: sf org login web")

    if org_info.get("status", 0) != 0:
        message = org_info.get("message") or "Unknown Salesforce CLI error."
        die(f"Error: Salesforce CLI returned a failure status.\n  {message}")

    result = org_info.get("result", {})
    print(f"Connected to Salesforce org: {connected_org_display(result, target_org)}")

    instance_url = result.get("instanceUrl", "").rstrip("/")
    api_version = str(result.get("apiVersion", "") or "").strip()

    # `sf org display` no longer returns accessToken (Salesforce CLI security
    # update — see https://github.com/forcedotcom/cli/issues/3560). Fetch it
    # via the dedicated `sf org auth show-access-token` command instead.
    token_info_json = run_sf_org_auth_show_access_token(target_org, sf_invoker)
    if not token_info_json.strip():
        die("Error: Salesforce CLI returned empty output for show-access-token.")

    try:
        token_info = json.loads(token_info_json)
    except json.JSONDecodeError:
        die(
            "Error: Could not parse `sf org auth show-access-token --json` output.\n"
            "  Ensure the Salesforce CLI is up to date: `sf update`."
        )

    if token_info.get("status", 0) != 0:
        message = token_info.get("message") or "Unknown Salesforce CLI error."
        die(f"Error: Salesforce CLI returned a failure status fetching access token.\n  {message}")

    access_token = (token_info.get("result") or {}).get("accessToken", "")

    if not access_token or not instance_url:
        die("Error: Could not extract accessToken/instanceUrl from org info.\n  Run: sf org login web")

    if not api_version:
        die(
            "Error: Could not determine the org's API version from `sf org display`.\n"
            "  Ensure the Salesforce CLI is up to date and the org is reachable."
        )

    print(f"Using org API version: {api_version}")

    org = OrgContext(
        access_token=access_token,
        instance_url=instance_url,
        api_version=api_version,
    )

    print("Querying org metadata (this may take several minutes for large orgs)...")
    print("")

    print("Step 1/4  Fetching permissionable object list from PicklistValueInfo...", flush=True)
    picklist_object_names = set(OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS)
    picklist_object_names.update(fetch_picklist_object_names(org))

    if not picklist_object_names:
        die("Error: No objects returned. Check org connectivity.")

    print(
        f"         Found {len(picklist_object_names)} permissionable objects.",
        flush=True,
    )

    print("\nStep 2/4  Fetching object list from EntityDefinition...", flush=True)
    entitydef_results = fetch_entity_definition_objects(org)

    if not entitydef_results:
        die("Error: No objects returned by EntityDefinition.")

    # Full set of all EntityDefinition names (used for objectPermissions cross-reference).
    all_entity_def_names = {name for name, _ in entitydef_results}
    # Track which objects support field-level security.
    fls_enabled_objects = {name for name, fls in entitydef_results if fls}

    print(
        f"         Found {len(all_entity_def_names)} objects "
        f"({len(fls_enabled_objects)} with FLS enabled).",
        flush=True,
    )

    # Only scan objects for permissionable fields if they support FLS and
    # are not excluded by event/predefined rules.
    field_scan_objects = sorted(
        obj for obj in fls_enabled_objects if not is_excluded_object(obj)
    )

    excluded_event_count = sum(1 for obj in all_entity_def_names if is_excluded_event_object(obj))
    excluded_predefined_count = sum(1 for obj in all_entity_def_names if is_excluded_predefined_object(obj))
    excluded_no_fls_count = sum(
        1 for obj in all_entity_def_names
        if not is_excluded_object(obj) and obj not in fls_enabled_objects
    )
    print(
        f"         Excluded {excluded_event_count} event objects, "
        f"{excluded_predefined_count} predefined objects, "
        f"{excluded_no_fls_count} non-FLS objects.",
        flush=True,
    )

    # The broader set of non-excluded EntityDefinition objects is used for
    # objectPermissions cross-referencing (objects need not support FLS to
    # have valid objectPermissions entries).
    valid_object_names = {
        obj for obj in all_entity_def_names if not is_excluded_object(obj)
    }
    # Case-insensitive lookups from lowercase API name -> canonical org casing.
    canonical_entity_lookup = {name.lower(): name for name in all_entity_def_names}
    valid_canonical_lookup = {name.lower(): name for name in valid_object_names}
    # objectPermissions values come from a permission picklist; keep only values
    # that also resolve as concrete objects in the exclusion-filtered EntityDefinition
    # set. Matching is case-insensitive so constants like
    # OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS need not match the org's exact casing.
    seen_object_permission_names = set()
    object_permission_names = []
    for obj in picklist_object_names:
        canonical = valid_canonical_lookup.get((obj or "").lower())
        if canonical and canonical not in seen_object_permission_names:
            object_permission_names.append(canonical)
            seen_object_permission_names.add(canonical)

    print(
        f"         Using {len(object_permission_names)} objects for objectPermissions.",
        flush=True,
    )

    permissionable_fields = set()
    total_batches = (len(field_scan_objects) + BATCH_SIZE - 1) // BATCH_SIZE

    print(
        f"\nStep 3/4  Querying EntityParticle in {total_batches} batch(es) "
        f"of up to {BATCH_SIZE} objects...",
        flush=True,
    )

    for batch_idx in range(0, len(field_scan_objects), BATCH_SIZE):
        batch = field_scan_objects[batch_idx: batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1
        print(f"  [{batch_num:>3}/{total_batches}] {batch[0]} – {batch[-1]}", flush=True)

        in_list = ", ".join(f"'{n}'" for n in batch)
        soql = (
            "SELECT EntityDefinition.QualifiedApiName, QualifiedApiName, IsComponent "
            "FROM EntityParticle "
            "WHERE IsPermissionable = true "
            f"AND EntityDefinition.QualifiedApiName IN ({in_list}) "
            "ORDER BY QualifiedApiName"
        )

        for rec in tooling_query(soql, org):
            entity = rec.get("EntityDefinition") or {}
            obj_name = entity.get("QualifiedApiName", "").strip()
            field_name = rec.get("QualifiedApiName", "").strip()
            if not obj_name or not field_name:
                continue

            # Compound component fields (e.g. BillingStateCode) are not
            # valid in permission set fieldPermissions entries.
            if bool(rec.get("IsComponent")):
                continue

            # Person Account alias fields (Account.*__pc) mirror Contact custom fields.
            # Exclude them to avoid redundant fieldPermissions entries.
            if obj_name == "Account" and field_name.endswith("__pc"):
                continue

            permissionable_fields.add(f"{obj_name}.{field_name}")

    # Some objects (e.g. EmailMessage) are absent from EntityParticle.
    # Fall back to FieldDefinition (one object at a time) for those.
    if FIELD_QUERY_SUPPLEMENTAL_OBJECTS:
        print(
            f"\n         Querying FieldDefinition for {len(FIELD_QUERY_SUPPLEMENTAL_OBJECTS)} additional object(s)...",
            flush=True,
        )
    for fd_obj in FIELD_QUERY_SUPPLEMENTAL_OBJECTS:
        print(f"           {fd_obj}", flush=True)
        fd_soql = (
            "SELECT QualifiedApiName, EntityDefinition.QualifiedApiName "
            "FROM FieldDefinition "
            "WHERE IsFlsEnabled = TRUE "
            f"AND EntityDefinition.QualifiedApiName = '{fd_obj}'"
        )
        for rec in tooling_query(fd_soql, org):
            entity = rec.get("EntityDefinition") or {}
            obj_name = entity.get("QualifiedApiName", "").strip()
            field_name = rec.get("QualifiedApiName", "").strip()
            if not obj_name or not field_name:
                continue
            permissionable_fields.add(f"{obj_name}.{field_name}")

    # Inject explicitly-listed supplemental fields that are valid in
    # fieldPermissions but absent from both EntityParticle and FieldDefinition
    # (for example DisputeItemChargeback.CardBrand). Entries whose parent
    # object is excluded or not present in the org are skipped so the
    # generated permission set always deploys cleanly.
    if FIELD_PERMISSION_SUPPLEMENTAL_FIELDS:
        injected = 0
        skipped_missing = []
        existing_fields_lower = {f.lower() for f in permissionable_fields}
        for field_api in FIELD_PERMISSION_SUPPLEMENTAL_FIELDS:
            if "." not in field_api:
                continue
            obj_name, field_name = field_api.split(".", 1)
            if is_excluded_object(obj_name):
                continue
            canonical_obj = canonical_entity_lookup.get(obj_name.lower())
            if not canonical_obj:
                skipped_missing.append(field_api)
                continue
            canonical_field_api = f"{canonical_obj}.{field_name}"
            if canonical_field_api.lower() not in existing_fields_lower:
                permissionable_fields.add(canonical_field_api)
                existing_fields_lower.add(canonical_field_api.lower())
                injected += 1
        if injected:
            print(
                f"\n         Injected {injected} supplemental field(s) from "
                "FIELD_PERMISSION_SUPPLEMENTAL_FIELDS.",
                flush=True,
            )
        if skipped_missing:
            print(
                f"         Skipped {len(skipped_missing)} supplemental field(s) "
                "whose parent object is not present in this org: "
                f"{', '.join(sorted(skipped_missing))}",
                flush=True,
            )

    # Enforce predefined object exclusions at field level as a final guard.
    if EXCLUDED_OBJECT_API_NAMES:
        permissionable_fields = {
            field_api
            for field_api in permissionable_fields
            if not is_excluded_predefined_object(field_api.split(".", 1)[0])
        }

    print(f"\n         Total permissionable fields: {len(permissionable_fields)}", flush=True)

    if not permissionable_fields:
        die("Error: No permissionable fields found. Check org permissions.")

    sorted_fields = sorted(permissionable_fields)
    sorted_objects = sorted(object_permission_names)

    record_types = set()
    object_scope = set(sorted_objects)

    print("\nStep 4/4  Fetching record types via Metadata API...", flush=True)

    record_type_full_names = metadata_list_recordtype_fullnames(org)
    print(
        f"         Found {len(record_type_full_names)} record types from Metadata API.",
        flush=True,
    )

    for full_name in record_type_full_names:
        if "." not in full_name:
            continue
        object_api, record_type_api = full_name.split(".", 1)

        if is_excluded_predefined_object(object_api) or is_excluded_event_object(object_api):
            continue

        normalized_full_name = full_name
        if object_api == "Account" and record_type_api == "PersonAccount":
            # Metadata can return this record type under Account; normalize to the
            # PersonAccount-scoped full name used in permission set XML.
            normalized_full_name = "PersonAccount.PersonAccount"
            object_api = "PersonAccount"

        if is_excluded_predefined_object(object_api) or is_excluded_event_object(object_api):
            continue

        # Include PersonAccount when Account is in scope because person accounts
        # are represented through Account in many metadata contexts.
        in_scope_via_account = object_api == "PersonAccount" and "Account" in object_scope
        if object_api in object_scope or in_scope_via_account:
            record_types.add(normalized_full_name)

    sorted_record_types = sorted(record_types)
    print(f"         Using {len(sorted_record_types)} record types in scope.", flush=True)

    root = ET.Element(qtag("PermissionSet"))
    desc_elem = ET.Element(qtag("description"))
    desc_elem.text = PERMSET_DESC
    activation_elem = ET.Element(qtag("hasActivationRequired"))
    activation_elem.text = "false"
    label_elem = ET.Element(qtag("label"))
    label_elem.text = PERMSET_LABEL
    metadata_elements = [desc_elem, activation_elem, label_elem]

    if output_file.exists():
        print(f"\nRecreating permission set with {len(sorted_fields)} field entries.")
    else:
        print(f"\nCreating new permission set with {len(sorted_fields)} field entries.")

    field_perm_elements = []
    for field_api in sorted_fields:
        field_perm = ET.Element(qtag("fieldPermissions"))
        ET.SubElement(field_perm, qtag("editable")).text = "true"
        ET.SubElement(field_perm, qtag("field")).text = field_api
        ET.SubElement(field_perm, qtag("readable")).text = "true"
        field_perm_elements.append(field_perm)

    obj_perm_elements = []
    for object_api in sorted_objects:
        obj_perm = ET.Element(qtag("objectPermissions"))
        ET.SubElement(obj_perm, qtag("allowCreate")).text = "true"
        ET.SubElement(obj_perm, qtag("allowDelete")).text = "false"
        ET.SubElement(obj_perm, qtag("allowEdit")).text = "false"
        ET.SubElement(obj_perm, qtag("allowRead")).text = "true"
        ET.SubElement(obj_perm, qtag("modifyAllRecords")).text = "false"
        ET.SubElement(obj_perm, qtag("object")).text = object_api
        ET.SubElement(obj_perm, qtag("viewAllRecords")).text = "false"
        obj_perm_elements.append(obj_perm)

    rt_vis_elements = []
    for record_type_name in sorted_record_types:
        rt_vis = ET.Element(qtag("recordTypeVisibilities"))
        ET.SubElement(rt_vis, qtag("recordType")).text = record_type_name
        ET.SubElement(rt_vis, qtag("visible")).text = "true"
        rt_vis_elements.append(rt_vis)

    user_perm_elements = []
    for permission_name in sorted(USER_PERMISSION_NAMES):
        user_perm = ET.Element(qtag("userPermissions"))
        ET.SubElement(user_perm, qtag("enabled")).text = "true"
        ET.SubElement(user_perm, qtag("name")).text = permission_name
        user_perm_elements.append(user_perm)

    all_children = metadata_elements + field_perm_elements + obj_perm_elements + rt_vis_elements + user_perm_elements
    all_children.sort(key=sort_key)

    for child in all_children:
        root.append(child)

    ET.indent(root, space="    ")

    # Build the document with a fixed Salesforce-style declaration rather than
    # relying on ElementTree's declaration formatting, which varies by version.
    body = ET.tostring(root, encoding="unicode")
    raw = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    if not raw.endswith("\n"):
        raw += "\n"

    try:
        with output_file.open("w", encoding="utf-8") as f:
            f.write(raw)
    except OSError as exc:
        die(f"Error: Failed to write permission set file `{output_file}`: {exc}")

    print(f"\nPermission set written: {output_file}")
    print(f"  objectPermissions:      {len(sorted_objects)}")
    print(f"  fieldPermissions:       {len(sorted_fields)}")
    print(f"  recordTypeVisibilities: {len(sorted_record_types)}")
    print(f"  userPermissions:        {len(user_perm_elements)}")
    print("")

    deployed = False
    validated = False
    if args.check_only:
        print(
            f"Validating generated permission set (check-only, wait up to {args.deploy_wait} minute(s))..."
        )
        run_sf_project_deploy(output_file, target_org, sf_invoker, args.deploy_wait, check_only=True)
        validated = True
        print("Validation completed successfully (no changes were deployed).")
    elif args.deploy:
        print(
            f"Deploying generated permission set (wait up to {args.deploy_wait} minute(s))..."
        )
        run_sf_project_deploy(output_file, target_org, sf_invoker, args.deploy_wait)
        deployed = True
        print("Deployment command completed successfully.")

    print("Done!")
    if not deployed:
        print(f'Deploy with: sf project deploy start --ignore-conflicts --source-dir "{output_file}"')

    if args.json:
        sys.stdout = original_stdout
        summary = {
            "permissionSet": PERMSET_NAME,
            "outputFile": str(output_file),
            "targetOrg": target_org,
            "apiVersion": api_version,
            "deployed": deployed,
            "validated": validated,
            "counts": {
                "objectPermissions": len(sorted_objects),
                "fieldPermissions": len(sorted_fields),
                "recordTypeVisibilities": len(sorted_record_types),
                "userPermissions": len(user_perm_elements),
            },
        }
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ScriptError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)
