# Permishifizer 9000

![Permishifizer 9000 banner](./assets/banner.png)

Generate a **SalesforceBackup** permission set XML from live org metadata — ensuring your backup/restore integration user has access to every restorable object, field, and record type.

---

## Table of Contents

- [Permishifizer 9000](#permishifizer-9000)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [The Problem It Solves](#the-problem-it-solves)
  - [What Gets Generated](#what-gets-generated)
  - [What Gets Excluded](#what-gets-excluded)
  - [Quick Start Guide](#quick-start-guide)
    - [Prerequisites](#prerequisites)
    - [Installation](#installation)
    - [Quick Start](#quick-start)
    - [Post-Installation Steps](#post-installation-steps)
  - [Usage](#usage)
    - [Command-Line Reference](#command-line-reference)
    - [Target Org Resolution](#target-org-resolution)
    - [Custom Output Path](#custom-output-path)
    - [Automatic Deployment](#automatic-deployment)
    - [Validate-Only (Check-Only) Deployment](#validate-only-check-only-deployment)
    - [Excluding Additional Objects](#excluding-additional-objects)
    - [Supplementing Object Permissions](#supplementing-object-permissions)
    - [Supplementing Field Queries](#supplementing-field-queries)
    - [Supplementing Individual Field Permissions](#supplementing-individual-field-permissions)
    - [Configuring User Permissions](#configuring-user-permissions)
    - [JSON Output](#json-output)
  - [Output](#output)
    - [Console Output](#console-output)
  - [CI/CD Integration](#cicd-integration)
    - [Azure DevOps Pipelines](#azure-devops-pipelines)
    - [GitHub Actions](#github-actions)
    - [GitLab CI](#gitlab-ci)
    - [Bitbucket Pipelines](#bitbucket-pipelines)
    - [Jenkins (Declarative Pipeline)](#jenkins-declarative-pipeline)
    - [Environment Variable Authentication (All Platforms)](#environment-variable-authentication-all-platforms)
  - [How It Works](#how-it-works)
    - [Key Design Decisions](#key-design-decisions)
  - [Troubleshooting](#troubleshooting)
  - [Configuration Reference](#configuration-reference)
  - [About the Creator](#about-the-creator)

---

## Overview

`permishifizer9000.py` connects to an authenticated Salesforce org, introspects its full metadata surface, and writes a `SalesforceBackup.permissionset-meta.xml` file that grants a user the minimum access needed for comprehensive backup and restore operations across all standard and custom objects. It's for Salesforce admins and developers configuring a backup/restore integration user — whether they use a dedicated tool such as Own from Salesforce (formerly OwnBackup) or run backups manually. _And it's got a cool name!_

## The Problem It Solves

Backup and restore tools require an integration user that can read every restorable object, field, and record type in the org — a permission surface that is impractical to assemble by hand. Building that permission set manually is tedious and error-prone, and it drifts out of sync as objects, fields, and record types are added or changed. Permishifizer 9000 removes that maintenance burden by deriving the full permission surface directly from live org metadata, **always regenerated from scratch** so it stays aligned with the current org schema.

## What Gets Generated

| Permission Type          | Scope                                                        | Access Granted                                                                 |
|--------------------------|--------------------------------------------------------------|--------------------------------------------------------------------------------|
| `objectPermissions`      | All permissionable objects in the org                        | `allowRead=true`, `allowCreate=true`                                           |
| `fieldPermissions`       | All permissionable fields on those objects                   | `readable=true`; `editable=true` for all permissionable fields                  |
| `recordTypeVisibilities` | All record types on included objects (including PersonAccount) | `visible=true`                                                                 |
| `userPermissions`        | Configured user-level permissions in `USER_PERMISSION_NAMES` | `enabled=true`                                                                  |

## What Gets Excluded

The following categories are **intentionally excluded** because they are event streams, not restorable record data:

- **Change Data Capture objects** — API names ending in `ChangeEvent`
- **Platform Event objects** — API names ending in `__e`
- **Predefined exclusions** — any API names listed in the `EXCLUDED_OBJECT_API_NAMES` set within the script
- **Compound component fields** — component field APIs (for example `BillingStateCode`) are skipped because they are not valid in `fieldPermissions`; compatible non-component fields are used instead
- **Person Account alias fields** — `Account.*__pc` fields (mirrors of Contact custom fields) are skipped to avoid duplicate entries

## Quick Start Guide

### Prerequisites

| Requirement             | Details                                                                                          |
|-------------------------|--------------------------------------------------------------------------------------------------|
| **Python**              | 3.12 or later                                                                                    |
| **Salesforce CLI (`sf`)** | Installed and on `PATH`. [Install guide](https://developer.salesforce.com/tools/salesforcecli) |
| **Authenticated org**   | At least one org authenticated via `sf org login web` or `sf org login sfdx-url`                |
| **User Permissions**    | The authenticated user requires: <br> • **API Enabled** (`ApiEnabled`) <br> • **Customize Application** (`CustomizeApplication`) <br> • **Manage Profiles and Permission Sets** (`ManageProfilesPermissionsets`) <br> • **Modify Metadata Through Metadata API Functions** (`ModifyMetadata`) |

> **Note:** No third-party Python packages are required — the script uses only the standard library.

### Installation

Clone the repository (or copy the script) into your project:

```bash
git clone <repository-url> permishifizer9000
cd permishifizer9000
```

### Quick Start

```bash
# Use the default target org (sf CLI default)
python permishifizer9000.py

# Recommended (explicit): specify an org alias
python permishifizer9000.py --target-org myOrgAlias
python permishifizer9000.py -o myOrgAlias

# Recommended (explicit): specify an org username
python permishifizer9000.py --target-org admin@example.com

# Convenience shorthand (also supported): positional target org
python permishifizer9000.py myOrgAlias

# Generate and automatically deploy the permission set
python permishifizer9000.py --target-org myOrgAlias --deploy
python permishifizer9000.py -o myOrgAlias -d

# Generate and deploy with a custom deploy wait window (minutes)
python permishifizer9000.py --target-org myOrgAlias --deploy --deploy-wait 20
python permishifizer9000.py -o myOrgAlias -d -w 20

# Custom output path using shorthand
python permishifizer9000.py -o myOrgAlias -f ./output/SalesforceBackup.permissionset-meta.xml
```

The generated permission set is written to:

```
./force-app/main/default/permissionsets/SalesforceBackup.permissionset-meta.xml
```

### Post-Installation Steps

Deploy it immediately:

```bash
sf project deploy start --ignore-conflicts --source-dir "force-app/main/default/permissionsets/SalesforceBackup.permissionset-meta.xml"
```

Assign to a user:

```bash
sf org assign permset --name SalesforceBackup --target-org myOrgAlias
```

## Usage

### Command-Line Reference

```
usage: permishifizer9000.py [-h] [--target-org TARGET_ORG] [--output-file OUTPUT_FILE] [--deploy] [--deploy-wait DEPLOY_WAIT] [--json] [--check-only] [target_org]

positional arguments:
  target_org              Optional Salesforce org alias/username (convenience shorthand)

options:
  -h, --help              Show this help message and exit
  --target-org TARGET_ORG, -o TARGET_ORG
                          Salesforce org alias/username (preferred for CI/CD; same meaning as positional target_org)
  --output-file OUTPUT_FILE, -f OUTPUT_FILE
                          Output permission set XML path (default: force-app/main/default/permissionsets/SalesforceBackup.permissionset-meta.xml)
  --deploy, -d            Automatically deploy the generated permission set after writing it
  --deploy-wait DEPLOY_WAIT, -w DEPLOY_WAIT
                          Minutes to wait for deployment completion when using --deploy (default: 10). Must be >= 1.
  --json, -j              Emit a machine-readable JSON summary to stdout; progress is sent to stderr
  --check-only, -c        Validate the generated permission set against the org (dry-run) without deploying it
```

### Target Org Resolution

The script resolves the target org in the following priority order:

| Priority | Source                          | Example                                             |
|----------|---------------------------------|-----------------------------------------------------|
| 1        | `--target-org` flag             | `python permishifizer9000.py --target-org myOrg`|
| 2        | Positional argument             | `python permishifizer9000.py myOrg`             |
| 3        | `SF_TARGET_ORG` environment var | `export SF_TARGET_ORG=myOrg`                        |
| 4        | Salesforce CLI default org      | Set via `sf config set target-org=myOrg`             |

### Custom Output Path

Write the permission set to a different location:

```bash
python permishifizer9000.py --output-file ./output/BackupPerms.permissionset-meta.xml
python permishifizer9000.py -f ./output/BackupPerms.permissionset-meta.xml
```

### Automatic Deployment

To deploy immediately after generation, add `--deploy`:

```bash
python permishifizer9000.py --target-org myOrg --deploy
```

When `--deploy` is used, the script runs `sf project deploy start --ignore-conflicts` for the generated permission set file.

Before any org/API processing starts, deploy mode performs a preflight validation and fails fast if either condition is not met:

- `sfdx-project.json` can be located at project root
- The generated output file path is inside one of the `packageDirectories[].path` roots from `sfdx-project.json`

This prevents long-running metadata generation when deployment cannot succeed due to project structure.

By default, the script waits up to 10 minutes for deployment completion. To change that (minimum 1 minute):

```bash
python permishifizer9000.py --target-org myOrg --deploy --deploy-wait 20
```

### Validate-Only (Check-Only) Deployment

To verify that the generated permission set *would* deploy — without persisting any changes to the org — use `--check-only` (`-c`):

```bash
python permishifizer9000.py --target-org myOrg --check-only
```

This runs `sf project deploy start --dry-run`, which validates the metadata against the org and reports success or failure without committing it. `--check-only` and `--deploy` are mutually exclusive.

Like `--deploy`, check-only mode performs the same preflight validation (`sfdx-project.json` at project root and the output file inside a configured package directory) and honors `--deploy-wait`.

### Excluding Additional Objects

Edit the `EXCLUDED_OBJECT_API_NAMES` set in the script to exclude specific objects from the generated permission set:

```python
EXCLUDED_OBJECT_API_NAMES = {
    "Task",
    "Event",
    "CustomObject__c",
    "acme__Subscription__c",
}
```

All excluded objects are removed from `objectPermissions`, `fieldPermissions`, and `recordTypeVisibilities`.

### Supplementing Object Permissions

Some objects are valid for `objectPermissions` but are not returned by the PicklistValueInfo query that the script uses in Step 1. Salesforce handles these objects specially and omits them from the picklist. Edit the `OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS` set in the script to ensure they are included:

```python
OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS = {"Task", "Event"}
```

These names are merged into the Step 1 result set before the cross-reference against EntityDefinition, so they appear in the generated `objectPermissions` as long as they also exist as non-excluded objects in the org.

### Supplementing Field Queries

Some objects have permissionable fields that are not indexed in the `EntityParticle` Tooling API table the script queries in Step 3, or are filtered out of the EntityParticle scan because `EntityDefinition.IsFlsEnabled = false` even though they have permissionable custom fields (for example `User`, whose standard fields are governed by user-management permissions rather than the `<fieldPermissions>` framework). For these objects, the script falls back to querying `FieldDefinition` one object at a time. Edit the `FIELD_QUERY_SUPPLEMENTAL_OBJECTS` list in the script to add or remove objects that need this treatment:

```python
FIELD_QUERY_SUPPLEMENTAL_OBJECTS = [
    "EmailMessage",
    "User",
]
```

Fields returned for these objects are added to the generated `fieldPermissions` alongside the regular EntityParticle results.

### Supplementing Individual Field Permissions

A small number of fields are valid in `fieldPermissions` (and selectable in the Salesforce permission set UI) but are not returned by either `EntityParticle` or `FieldDefinition` — for example `DisputeItemChargeBack.CardBrand`. Add fully-qualified field API names to `FIELD_PERMISSION_SUPPLEMENTAL_FIELDS` in the script to inject them verbatim:

```python
FIELD_PERMISSION_SUPPLEMENTAL_FIELDS = {
    "DisputeItemChargeBack.CardBrand",
}
```

Each entry must use the `Object.Field` format. Entries whose parent object is excluded (event objects, predefined exclusions) are skipped.

### Configuring User Permissions

Edit the `USER_PERMISSION_NAMES` set in the script to add/remove generated `userPermissions` entries:

```python
USER_PERMISSION_NAMES = {
  "AllowViewEditConvertedLeads",
  "ModifyAllData",
  "ViewAllData",
}
```

Depending on your backup/restore setup, additional permissions may be required. For guidance, see Salesforce Help: https://help.salesforce.com/s/articleView?id=platform.backup_recover_o_auth_user_perms.htm&type=5

Each entry generates:

```xml
<userPermissions>
  <enabled>true</enabled>
  <name>AllowViewEditConvertedLeads</name>
</userPermissions>
```

### JSON Output

Add `--json` (`-j`) to emit a machine-readable summary to **stdout** after the run. All human-readable progress is redirected to **stderr**, so stdout carries only the JSON document — safe to parse in a pipeline.

```bash
python permishifizer9000.py --target-org myOrg --json
```

Example output:

```json
{
  "permissionSet": "SalesforceBackup",
  "outputFile": "/path/to/force-app/main/default/permissionsets/SalesforceBackup.permissionset-meta.xml",
  "targetOrg": "myOrg",
  "apiVersion": "67.0",
  "deployed": false,
  "validated": false,
  "counts": {
    "objectPermissions": 374,
    "fieldPermissions": 8342,
    "recordTypeVisibilities": 54,
    "userPermissions": 52
  }
}
```

`targetOrg` is `null` when the org is resolved from the Salesforce CLI default. `deployed` is `true` only when `--deploy` runs and succeeds; `validated` is `true` only when `--check-only` runs and succeeds. The [exit codes](#cicd-integration) are unchanged, so pipelines can branch on both the JSON payload and the process exit status.

## Output

The script produces a standard Salesforce metadata XML file. A condensed example:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <description>Grants object, field, and record type access for full-record backup/restore scope (excluding ChangeEvent and __e event objects).</description>
    <fieldPermissions>
        <editable>true</editable>
        <field>Account.Name</field>
        <readable>true</readable>
    </fieldPermissions>
    <!-- ... more fieldPermissions ... -->
    <hasActivationRequired>false</hasActivationRequired>
    <label>Salesforce Backup</label>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>false</allowDelete>
        <allowEdit>false</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Account</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <!-- ... more objectPermissions ... -->
    <recordTypeVisibilities>
        <recordType>Account.MyRecordType</recordType>
        <visible>true</visible>
    </recordTypeVisibilities>
    <!-- ... more recordTypeVisibilities ... -->
     <userPermissions>
        <enabled>true</enabled>
        <name>AllowViewEditConvertedLeads</name>
    </userPermissions>
    <!-- ... more userPermissions ... -->
</PermissionSet>
```

### Console Output

During execution the script prints a progress summary:

```
Connecting to Salesforce org...
Querying org metadata (this may take several minutes for large orgs)...

Step 1/4  Fetching permissionable object list from PicklistValueInfo...
         Found 412 permissionable objects.

Step 2/4  Fetching object list from EntityDefinition...
         Found 1247 objects (892 with FLS enabled).
         Excluded 38 event objects, 0 predefined objects, 355 non-FLS objects.
         Using 374 objects for objectPermissions.

Step 3/4  Querying EntityParticle in 25 batch(es) of up to 50 objects...
  [  1/ 25] Account – Contact
  ...

         Total permissionable fields: 8342

Step 4/4  Fetching record types via Metadata API...
         Found 67 record types from Metadata API.
         Using 54 record types in scope.

Permission set written: .../SalesforceBackup.permissionset-meta.xml
  objectPermissions:      374
  fieldPermissions:       8342
  recordTypeVisibilities: 54
  userPermissions:        52

Done!
Deploy with: sf project deploy start --ignore-conflicts --source-dir "..."
```

## CI/CD Integration

The script is designed for automated pipelines. It has **zero Python dependencies** beyond the standard library and exits with meaningful error codes. Add `--json` for a machine-readable run summary you can assert on (see [JSON Output](#json-output)).

All CI examples below use `python permishifizer9000.py --deploy`, which already invokes `sf project deploy start --ignore-conflicts` internally.
If you switch to a direct CLI deploy step, use `sf project deploy start --ignore-conflicts --source-dir "..."`.

| Exit Code | Meaning                          |
|-----------|----------------------------------|
| `0`       | Success                          |
| `1`       | Script error (auth, network, etc.)|
| `130`     | Interrupted by user (Ctrl+C)     |

### Azure DevOps Pipelines

```yaml
trigger: none

schedules:
  - cron: "0 6 * * 1"
    displayName: Weekly Monday 06:00 UTC
    branches:
      include:
        - main

pool:
  vmImage: "ubuntu-latest"

steps:
  - task: UseNode@1
    inputs:
      version: "20.x"

  - task: UsePythonVersion@0
    inputs:
      versionSpec: "3.12"

  - script: npm install -g @salesforce/cli
    displayName: Install Salesforce CLI

  - script: |
      echo "$(SF_AUTH_URL)" > auth_url.txt
      sf org login sfdx-url --sfdx-url-file auth_url.txt --set-default --alias ci-org
      rm auth_url.txt
    displayName: Authenticate to Salesforce

  - script: python permishifizer9000.py --target-org ci-org --deploy --deploy-wait 10
    displayName: Generate and deploy permission set

  - script: sf org assign permset --name SalesforceBackup --target-org ci-org
    displayName: Assign permission set
```

### GitHub Actions

```yaml
name: Generate & Deploy Backup Permission Set

on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * 1"  # Weekly on Monday at 06:00 UTC

jobs:
  generate-backup-perms:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Salesforce CLI
        run: npm install -g @salesforce/cli

      - name: Authenticate to Salesforce
        run: |
          echo "${{ secrets.SF_AUTH_URL }}" > auth_url.txt
          sf org login sfdx-url --sfdx-url-file auth_url.txt --set-default --alias ci-org
          rm auth_url.txt

      - name: Generate and deploy permission set
        run: python permishifizer9000.py --target-org ci-org --deploy --deploy-wait 10

      - name: Assign permission set
        run: |
          sf org assign permset \
            --name SalesforceBackup \
            --target-org ci-org
```

### GitLab CI

```yaml
generate-backup-perms:
  image: node:20
  stage: deploy
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"
    - if: $CI_PIPELINE_SOURCE == "web"
  before_script:
    - npm install -g @salesforce/cli
    - apt-get update && apt-get install -y python3
    - echo "$SF_AUTH_URL" > auth_url.txt
    - sf org login sfdx-url --sfdx-url-file auth_url.txt --set-default --alias ci-org
    - rm auth_url.txt
  script:
    - python3 permishifizer9000.py --target-org ci-org --deploy --deploy-wait 10
    - sf org assign permset --name SalesforceBackup --target-org ci-org
```

### Bitbucket Pipelines

```yaml
pipelines:
  custom:
    generate-backup-perms:
      - step:
          name: Generate & Deploy Backup Permission Set
          image: node:20
          script:
            - npm install -g @salesforce/cli
            - apt-get update && apt-get install -y python3
            - echo "$SF_AUTH_URL" > auth_url.txt
            - sf org login sfdx-url --sfdx-url-file auth_url.txt --set-default --alias ci-org
            - rm auth_url.txt
            - python3 permishifizer9000.py --target-org ci-org --deploy --deploy-wait 10
            - sf org assign permset --name SalesforceBackup --target-org ci-org
```

### Jenkins (Declarative Pipeline)

```groovy
pipeline {
    agent any
    triggers {
        cron('H 6 * * 1')  // Weekly on Monday
    }
    environment {
        SF_AUTH_URL = credentials('sf-auth-url')
    }
    stages {
        stage('Setup') {
            steps {
                sh 'npm install -g @salesforce/cli'
            }
        }
        stage('Authenticate') {
            steps {
                sh '''
                    echo "$SF_AUTH_URL" > auth_url.txt
                    sf org login sfdx-url --sfdx-url-file auth_url.txt --set-default --alias ci-org
                    rm auth_url.txt
                '''
            }
        }
        stage('Generate') {
            steps {
            sh 'python3 permishifizer9000.py --target-org ci-org --deploy --deploy-wait 10'
            }
        }
        stage('Assign') {
            steps {
                sh 'sf org assign permset --name SalesforceBackup --target-org ci-org'
            }
        }
    }
}
```

### Environment Variable Authentication (All Platforms)

If you prefer environment-based org targeting without explicit `--target-org` flags:

```bash
export SF_TARGET_ORG="ci-org"
python permishifizer9000.py
```

The script picks up `SF_TARGET_ORG` automatically when no positional or flag argument is provided.

## How It Works

The script executes four sequential phases against the target org's APIs:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Step 1 — PicklistValueInfo (REST API)                                │
│ Fetches all permissionable object names from the ObjectPermissions   │
│ SobjectType picklist. Uses ordered keyset pagination (`ORDER BY` +   │
│ `Value > lastValue`) in pages of 2,000 rows. Merges in any names     │
│ from `OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS` that Salesforce omits  │
│ from the picklist (e.g. Task, Event).                                │
├──────────────────────────────────────────────────────────────────────┤
│ Step 2 — EntityDefinition (Tooling API)                              │
│ Fetches all non-deprecated object names and FLS status via ordered   │
│ keyset paging (`QualifiedApiName > lastValue`), excludes event,      │
│ predefined, and non-FLS objects, and cross-references Step 1 to keep │
│ only concrete objects in scope for objectPermissions.                │
├──────────────────────────────────────────────────────────────────────┤
│ Step 3 — EntityParticle (Tooling API)                                │
│ Queries all permissionable fields in batches of 50 objects.          │
│ Marks included fields as editable in the generated permission set.   │
│ Excludes compound component fields (IsComponent=true), Person        │
│ Account alias fields (Account.*__pc), and any fields belonging to    │
│ predefined-excluded objects as a final guard. Objects listed in      │
│ `FIELD_QUERY_SUPPLEMENTAL_OBJECTS` are absent from EntityParticle;   │
│ their fields are fetched individually via FieldDefinition instead.   │
├──────────────────────────────────────────────────────────────────────┤
│ Step 4 — Metadata API (SOAP listMetadata)                            │
│ Retrieves all RecordType full names. Filters to objects in scope     │
│ and normalizes PersonAccount record types.                           │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
              Writes SalesforceBackup.permissionset-meta.xml
```

### Key Design Decisions

- **Full regeneration** — The XML file is always written from scratch, not patched, ensuring the output always reflects the current org state.
- **Keyset pagination** — Large object surfaces are fetched with deterministic, ordered pagination (`ORDER BY` + `> lastValue`) in `QUERY_PAGE_SIZE` chunks, reducing query count and complexity.
- **Permissionable-first field access** — Any field returned as `IsPermissionable=true` is emitted with `readable=true` and `editable=true` to maximize backup/restore coverage, including formula and other non-writable field types.
- **Compound compatibility guard** — `EntityParticle.IsComponent=true` fields are excluded so generated `fieldPermissions` only contain valid field names Salesforce accepts during deployment.
- **PersonAccount normalization** — `Account.PersonAccount` record types are normalized to `PersonAccount.PersonAccount` as required by permission set XML format.
- **No external dependencies** — Uses only the Python standard library (`urllib`, `xml.etree`, `json`, `subprocess`, etc.) to simplify CI/CD setup.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Error: Salesforce CLI (sf) was not runnable` | `sf` not installed or not on `PATH` | Install the [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli) and restart your shell |
| `Error: Could not parse org display output` | Org not authenticated or token expired | Re-authenticate: `sf org login web --alias myOrg` |
| `Error: \`sf org auth show-access-token --json\` failed` | Salesforce CLI is outdated and lacks the `show-access-token` subcommand | Update the CLI: `sf update` (or reinstall from https://developer.salesforce.com/tools/salesforcecli) |
| `Error: HTTP 401 during REST query` | Access token expired mid-run | Re-authenticate and re-run |
| `Error: No objects returned` | Org connectivity issue or insufficient permissions | Verify the authenticated user has API and metadata access |
| `Error: Timeout after 90s` | Slow org or network | Retry; consider running closer to the Salesforce instance |
| `Error: Conflicting target org values` | Both positional and `--target-org` provided with different values | Use one or the other, not both with different values |
| `Error: --deploy and --check-only cannot be used together` | Both flags passed in the same run | Choose one: `--deploy` to deploy, or `--check-only` to validate only |
| `Error: Python 3.12+ is required` | Older Python version detected | Upgrade Python to 3.12 or later |
| `Error: --deploy requires sfdx-project.json at the project root` | Deploy mode could not find `sfdx-project.json` from output path/cwd/script directory | Run from a Salesforce DX project or provide an output path inside that project |
| `Error: --deploy requires the generated metadata file to be within a configured package directory` | `--output-file` is outside all `packageDirectories[].path` entries in `sfdx-project.json` | Use an output path under a configured package directory or update `packageDirectories` |
| Script runs but output has fewer objects than expected | Objects may be in `EXCLUDED_OBJECT_API_NAMES` or are event types | Review exclusions in the script; check console output for exclusion counts |

## Configuration Reference

These constants can be modified at the top of `permishifizer9000.py`:

| Constant                     | Default                           | Description                                                    |
|------------------------------|-----------------------------------|----------------------------------------------------------------|
| `PERMSET_NAME`               | `"SalesforceBackup"`              | API name of the generated permission set                       |
| `PERMSET_LABEL`              | `"Salesforce Backup"`             | Display label of the permission set                            |
| `PERMSET_DESC`               | *(see source)*                    | Description embedded in the permission set XML                 |
| `DEFAULT_PERMSET_FILE`       | `./force-app/main/.../SalesforceBackup.permissionset-meta.xml` | Default output path              |
| `EXCLUDED_OBJECT_API_NAMES`  | `set()` (empty)                   | Set of object API names to exclude from `objectPermissions`, `fieldPermissions`, and `recordTypeVisibilities` (does not affect `userPermissions`) |
| `OBJECT_PERMISSION_SUPPLEMENTAL_OBJECTS` | `{"Task", "Event"}`    | Objects valid for `objectPermissions` but missing from the PicklistValueInfo query; merged into the Step 1 result set before cross-referencing |
| `FIELD_QUERY_SUPPLEMENTAL_OBJECTS` | `["EmailMessage", "User"]` | Objects whose permissionable fields are not indexed in EntityParticle (or whose parent object reports `IsFlsEnabled = false`, e.g. `User`); their fields are fetched individually via FieldDefinition at the end of Step 3 |
| `FIELD_PERMISSION_SUPPLEMENTAL_FIELDS` | `{"DisputeItemChargeBack.CardBrand"}` | Fully-qualified field API names (`Object.Field`) injected verbatim into `fieldPermissions`; use for fields valid in permission sets but absent from both EntityParticle and FieldDefinition |
| `USER_PERMISSION_NAMES`      | *(see source)*                    | Set of user permission API names emitted as `userPermissions` entries |
| `BATCH_SIZE`                 | `50`                              | Number of objects per EntityParticle query batch                |
| `QUERY_PAGE_SIZE`            | `2000`                            | Page size for PicklistValueInfo and EntityDefinition keyset pagination |
| `HTTP_TIMEOUT_SECONDS`       | `90`                              | Timeout for each HTTP request to Salesforce                    |
| `SF_COMMAND_TIMEOUT_SECONDS` | `120`                             | Timeout for Salesforce CLI subprocess calls                    |
| `DEPLOY_TIMEOUT_BUFFER_SECONDS` | `300`                          | Extra seconds added to `deploy_wait × 60` for the deploy subprocess timeout |
| `MIN_PYTHON_VERSION`         | `(3, 12)`                         | Minimum required Python version; script exits if the runtime is older |
| `TARGET_ORG_ENV_VAR`         | `"SF_TARGET_ORG"`                 | Environment variable checked for target org fallback           |

## About the Creator

Built by [@dcstuff](https://github.com/dcstuff) as part of the Trailblazer Labs Builder in Residence Cohort.
