# Vertex AI Workbench Fleet Upgrader — Wiki

## Table of Contents

- [Project Overview](#project-overview)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Core Modules](#core-modules)
- [Data Models](#data-models)
- [CLI Reference](#cli-reference)
- [Bash Wrappers](#bash-wrappers)
- [Cloud Function Deployment](#cloud-function-deployment)
- [Cloud Function API Reference](#cloud-function-api-reference)
- [Terraform Infrastructure](#terraform-infrastructure)
- [Configuration](#configuration)
- [Upgrade Workflow](#upgrade-workflow)
- [Rollback Workflow](#rollback-workflow)
- [Safety Features](#safety-features)
- [Testing](#testing)
- [Development Setup](#development-setup)
- [Troubleshooting](#troubleshooting)

---

## Project Overview

The **Vertex AI Workbench Fleet Upgrader** (`fleet-upgrade-scripts`) is a Python tool that automates upgrading and rolling back
Google Cloud Vertex AI Workbench instances at scale. It uses the native Notebooks v2 REST API to manage fleet-wide operations
across multiple GCP zones, with support for parallel processing, health checks, automatic rollback on failure, and detailed reporting.

**Key capabilities:**
- Fleet-wide or single-instance upgrades and rollbacks
- Parallel processing with configurable concurrency and stagger delays
- Pre/post-operation health verification
- Automatic rollback on upgrade failure (opt-in)
- Dedicated rollback mode with comprehensive pre-checks
- Dry-run mode that reports planned actions without upgrading or
  rolling back (still starts STOPPED/SUSPENDED instances to evaluate them)
- Auto-start of STOPPED/SUSPENDED instances before operations
  (including in dry-run)
- JSON reports and structured logging
- Serverless deployment via Google Cloud Functions (Gen 2)

**Tech stack:** Python 3.11+, Google Cloud Notebooks v2 API, Terraform 1.14+, Cloud Functions Gen 2, pytest

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Entry Points                             │
│                                                                 │
│  main.py          wb-upgrade.sh / wb-rollback.sh    Cloud Func  │
│  (direct CLI)     (bash wrappers with venv mgmt)    (HTTP API)  │
└──────┬──────────────────────┬───────────────────────────┬───────┘
       │                      │                           │
       ▼                      ▼                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      src/ Core Library                          │
│                                                                 │
│  cli.py ──► config.py ──► upgrader.py / rollback.py             │
│                               │                                 │
│                               ▼                                 │
│                          clients.py (REST API client)           │
│                               │                                 │
│                               ▼                                 │
│                     models.py (data classes)                    │
│                     log_utils.py (logging setup)                │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                  Google Cloud Notebooks v2 API
                  (notebooks.googleapis.com/v2)
```

The project has two parallel codebases:
1. **`src/`** — CLI tool for local execution (writes log files and JSON reports to disk)
2. **`cloud_function/src/`** — Adapted for serverless execution (JSON structured logging, returns data via API response, no file I/O)

Both share the same core logic and data models but differ in logging, configuration, and output handling.

---

## Repository Structure

```
wbi-fleet-upgrade/
├── main.py                     # Direct Python entry point (adds src/ to sys.path)
├── wb-upgrade.sh               # Bash wrapper for upgrades (venv, auth, pre-flight)
├── wb-rollback.sh              # Bash wrapper for rollbacks
├── conftest.py                 # pytest config (adds src/ to sys.path)
├── pyproject.toml              # Package metadata and build config
├── pytest.ini                  # pytest settings (80% coverage threshold)
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Dev/test dependencies
├── .python-version             # Python 3.11.0
│
├── src/                        # Core library (CLI variant)
│   ├── cli.py                  # argparse CLI + console_scripts entry point
│   ├── clients.py              # WorkbenchRestClient (Notebooks v2 REST)
│   ├── config.py               # UpgraderConfig dataclass
│   ├── models.py               # InstanceRef, UpgradeResult, TrackedOp
│   ├── upgrader.py             # FleetUpgrader (upgrade orchestration)
│   ├── rollback.py             # FleetRollback (rollback orchestration + pre-checks)
│   └── log_utils.py            # Logging setup (file + stdout)
│
├── cloud_function/             # Cloud Function variant
│   ├── main.py                 # HTTP endpoints (Flask + functions-framework)
│   ├── requirements.txt        # Cloud Function dependencies
│   └── src/                    # Adapted core modules
│       ├── clients.py
│       ├── config.py           # CloudFunctionConfig (defaults dry_run=True)
│       ├── models.py
│       ├── upgrader.py
│       └── rollback.py
│
├── terraform/                  # Infrastructure as Code
│   ├── main.tf                 # Cloud Function + IAM + Storage
│   ├── variables.tf            # Configurable variables
│   ├── terraform.tfvars.example
│   └── deploy.sh               # Deployment helper script
│
└── tests/
    └── unit_tests/             # pytest unit tests
        ├── test_cli.py
        ├── test_clients.py
        ├── test_config.py
        ├── test_log_utils.py
        ├── test_models.py
        └── test_rollback_prechecks.py
```

---

## Core Modules

### `clients.py` — WorkbenchRestClient

REST client for the Notebooks v2 API (`notebooks.googleapis.com/v2`). Uses `google.auth` default credentials with `AuthorizedSession`.

| Method | Description |
|--------|-------------|
| `list_instances(location)` | List all Workbench instances in a zone (paginated) |
| `get_instance(instance_name)` | Get instance details by full resource name |
| `check_upgradability(instance_name)` | Check if an instance can be upgraded |
| `upgrade(instance_name)` | Initiate upgrade (returns operation name) |
| `rollback(instance_name, target_snapshot)` | Initiate rollback to a snapshot |
| `start_instance(instance_name)` | Start a stopped/suspended instance |
| `get_operation(op_name)` | Poll a long-running operation |
| `get_instance_by_name(instance_id, location)` | Lookup instance by short name + zone |

**Retry logic:** Exponential backoff with jitter for status codes `{409, 429, 500, 502, 503, 504}`. Respects `Retry-After` headers. Uses a longer base delay (15s) for 409 conflicts. Max delay capped at 180s.

### `upgrader.py` — FleetUpgrader

Orchestrates fleet-wide upgrades:
1. Scans instances across all specified zones
2. Pre-starts STOPPED/SUSPENDED instances in parallel
3. Checks upgradeability via the API
4. Starts upgrade operations with concurrency throttling and stagger delays
5. Polls operations until completion
6. Verifies health post-upgrade
7. Optionally rolls back failed upgrades
8. Generates statistics and per-instance results

### `rollback.py` — FleetRollback

Orchestrates fleet-wide rollbacks with comprehensive pre-checks:

**Pre-check pipeline (per instance):**
1. **Instance State** — Must be ACTIVE (critical)
2. **Upgrade History** — Must have a recent successful upgrade with a snapshot (critical)
3. **Snapshot Validity** — Snapshot resource name must be well-formed (critical)
4. **Rollback Window** — Checks timing of last upgrade (warning only)

After pre-checks pass, the rollback flow mirrors the upgrade flow: throttled parallel operations, polling, health verification, and reporting.

### `config.py` — UpgraderConfig / CloudFunctionConfig

Dataclass holding all operational parameters. Created from CLI args (`UpgraderConfig.from_args()`) or request body + environment variables (Cloud Function variant).

### `log_utils.py`

Sets up dual logging: stdout + rotating log file (`workbench-upgrade.log` or `workbench-rollback.log`). Supports verbose (DEBUG) mode.

---

## Data Models

Defined in `models.py`:

```python
@dataclass
class InstanceRef:
    name: str        # Full resource name: projects/.../locations/.../instances/...
    short_name: str  # Instance ID
    location: str    # Zone (e.g., europe-west2-a)

@dataclass
class UpgradeResult:
    instance_name: str
    location: str
    status: str      # "success", "failed", "skipped", "up_to_date", "dry_run"
    start_time: Optional[float]
    end_time: Optional[float]
    duration_seconds: Optional[float]
    target_version: Optional[str]
    error_message: Optional[str]
    rolled_back: bool

@dataclass
class TrackedOp:
    op_name: str     # Long-running operation ID
    instance: InstanceRef
    start_time: float
    target_version: str
```

---

## CLI Reference

### Direct Python execution

```bash
python3 main.py --project <project-id> --locations <zone1> [zone2 ...] [OPTIONS]
```

### Installed package (via pip)

```bash
workbench-upgrader --project <project-id> --locations <zone1> [zone2 ...] [OPTIONS]
```

### All options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | string | required | GCP project ID |
| `--locations` | string[] | required | Zone locations to scan |
| `--instance` | string | — | Target a single instance by ID |
| `--rollback` | flag | false | Rollback mode instead of upgrade |
| `--dry-run` | flag | false | Report planned actions without upgrading/rolling back (still starts stopped instances) |
| `--max-parallel` | int | 5 | Max concurrent operations |
| `--timeout` | int | 7200 | Per-operation timeout (seconds) |
| `--poll-interval` | int | 20 | Seconds between operation polls |
| `--rollback-on-failure` | flag | false | Auto-rollback failed upgrades |
| `--health-check-timeout` | int | 600 | Post-operation health check timeout |
| `--stagger-delay` | float | 3.0 | Delay between starting operations |
| `--verbose` | flag | false | Enable DEBUG logging |

---

## Bash Wrappers

### `wb-upgrade.sh`

Full-featured wrapper for upgrade operations:
- Automatic Python virtual environment creation and management
- GCP authentication validation (`gcloud auth list`)
- Pre-flight checks (Python version, required packages, API access)
- Colored terminal output with banners
- Environment variable support (`GCP_PROJECT_ID`, `LOCATIONS`, `INSTANCE_ID`, etc.)
- All CLI flags available as both arguments and env vars

```bash
# Using arguments
./wb-upgrade.sh --project my-project --locations "europe-west2-a europe-west2-b" --dry-run

# Using environment variables
export GCP_PROJECT_ID=my-project
export LOCATIONS="europe-west2-a europe-west2-b"
./wb-upgrade.sh --dry-run
```

### `wb-rollback.sh`

Same features as the upgrade wrapper, but invokes `main.py --rollback`.

```bash
./wb-rollback.sh --project my-project --locations "europe-west2-a" --instance my-notebook --dry-run
```

**Environment variables supported by both wrappers:**

| Variable | Description |
|----------|-------------|
| `GCP_PROJECT_ID` | GCP project ID |
| `LOCATIONS` | Space-separated zone list |
| `INSTANCE_ID` | Single instance target |
| `DRY_RUN` | `true`/`false` |
| `MAX_PARALLEL` | Max concurrent operations |
| `TIMEOUT` | Per-operation timeout |
| `POLL_INTERVAL` | Poll frequency |
| `HEALTH_CHECK_TIMEOUT` | Health check timeout |
| `STAGGER_DELAY` | Delay between operations |
| `VERBOSE` | `true`/`false` |
| `VENV_DIR` | Custom venv path |
| `PYTHON_CMD` | Custom Python interpreter |
| `USE_VENV` | `true`/`false` |
| `SKIP_VENV_CHECK` | Skip venv validation |

---

## Cloud Function Deployment

The tool can be deployed as a Google Cloud Function (Gen 2) for serverless, API-driven operations.

### Prerequisites

1. GCP project with billing enabled
2. APIs enabled: `cloudfunctions`, `cloudbuild`, `notebooks`, `storage`
3. Terraform >= 1.14
4. Authenticated `gcloud` CLI

### Deploy with Terraform

```bash
cd terraform/
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID and settings
terraform init
terraform plan
terraform apply
```

### What Terraform creates

- **Google Cloud Function (Gen 2)** with Flask HTTP trigger
- **Service Account** with a custom least-privilege IAM role (`wbi_fleet_upgrade_operator`) granting:
  - `notebooks.instances.list`, `get`, `checkUpgradability`, `upgrade`, `start`
  - `notebooks.operations.get`, `list`
- **Cloud Storage bucket** for function source code
- **IAM bindings** for function invocation (configurable `allowed_invokers`)
- **Logging writer** role for structured Cloud Logging

### Security

- IAM authentication required for all endpoints
- Input sanitization (null bytes, control characters, length limits)
- Validation of project IDs, zone formats, and instance IDs via regex
- Content-Type enforcement for POST requests
- `dry_run` defaults to `true` in the Cloud Function variant

---

## Cloud Function API Reference

All endpoints require an `Authorization: Bearer <identity-token>` header.

### `GET /`
Returns API information and available endpoints.

### `POST /upgrade`
Upgrade Workbench instances.

```json
{
  "project_id": "my-project",
  "locations": ["europe-west2-a", "europe-west2-b"],
  "instance_id": "my-instance",
  "dry_run": true,
  "max_parallel": 5,
  "timeout": 7200,
  "rollback_on_failure": false,
  "health_check_timeout": 600,
  "stagger_delay": 3.0
}
```

### `POST /rollback`
Rollback instances to previous version. Same request body as `/upgrade` (minus `rollback_on_failure`).

### `GET|POST /status`
Get current state of instances. Accepts `project_id`, `locations`, `instance_id` as query params or JSON body.

### `GET|POST /check-upgradability`
Check which instances are upgradeable. Returns per-instance upgrade availability and target versions.

### `GET /health`
Health check endpoint. Returns `200 OK`.

### Response format

```json
{
  "success": true,
  "timestamp": "2025-01-27T12:00:00.000Z",
  "message": "Dry run completed",
  "data": {
    "statistics": {
      "total": 5,
      "upgradeable": 2,
      "up_to_date": 3,
      "skipped": 0,
      "upgrade_started": 0,
      "upgraded": 0,
      "failed": 0,
      "rolled_back": 0
    },
    "results": [...]
  }
}
```

---

## Terraform Infrastructure

### Variables (`variables.tf`)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `project_id` | string | required | GCP project for deployment |
| `region` | string | `europe-west2` | Cloud Function region |
| `function_name` | string | `wbi-fleet-upgrade` | Function name |
| `target_project_ids` | list(string) | `[]` | Projects to manage (defaults to deploying project) |
| `target_locations` | list(string) | `[europe-west2-a/b/c]` | Default zones to scan |
| `dry_run_default` | bool | `true` | Default dry-run mode |
| `max_parallel_default` | number | `5` | Default max concurrency |
| `timeout_seconds` | number | `540` | Function timeout |
| `memory_mb` | number | `512` | Memory allocation |
| `min_instances` | number | `0` | Min scaling (0 = cold start) |
| `max_instances` | number | `10` | Max scaling |
| `vpc_connector` | string | `""` | VPC connector for private networking |
| `service_account_email` | string | `""` | Existing SA (empty = create new) |
| `allowed_invokers` | list(string) | `[]` | IAM members allowed to invoke |
| `labels` | map(string) | `{managed-by, component}` | Resource labels |

---

## Configuration

### Runtime dependencies (`requirements.txt`)

```
google-cloud-notebooks>=1.15.0
google-api-core>=2.29.0
google-auth>=2.47.0
protobuf>=6.33.5
```

### Dev dependencies (`requirements-dev.txt`)

Extends runtime deps and adds: `pytest`, `pytest-cov`, `pytest-mock`, `mock`, `pylint`, `black`, `flake8`, `mypy`, `types-protobuf`

### Package installation

```bash
# Runtime only
pip install -r requirements.txt

# With dev tools
pip install -r requirements-dev.txt

# As installable package
pip install -e .
# Then use: workbench-upgrader --project ... --locations ...
```

---

## Upgrade Workflow

```
1. SCAN         → Find all instances in specified zones (or single instance)
2. PRE-START    → Start all STOPPED/SUSPENDED instances in parallel
3. CHECK        → Verify each instance is ACTIVE and ready
4. UPGRADEABLE? → Call checkUpgradability API
5. UPGRADE      → Start upgrade operations (throttled, staggered)
6. MONITOR      → Poll operations until complete or timeout
7. VERIFY       → Health check: wait for ACTIVE state
8. ROLLBACK?    → If --rollback-on-failure and upgrade failed, auto-rollback
9. REPORT       → Write logs + JSON report (upgrade-report-YYYYMMDD-HHMMSS.json)
```

**Instance state handling during upgrade:**
- `ACTIVE` → proceed with upgrade
- `STOPPED` / `SUSPENDED` → auto-start, wait for ACTIVE, then upgrade
- `PROVISIONING` / `STARTING` / `UPGRADING` / etc. → skip (busy)
- Dry-run mode starts STOPPED/SUSPENDED instances and reports
  upgradeability/target version, but does not perform the upgrade

---

## Rollback Workflow

```
1. SCAN         → Find instances
2. PRE-START    → Start STOPPED/SUSPENDED instances in parallel
3. PRE-CHECKS   → Run 4-step validation pipeline per instance:
   a. Instance State    (must be ACTIVE — critical)
   b. Upgrade History   (must have successful upgrade with snapshot — critical)
   c. Snapshot Validity (resource name format check — critical)
   d. Rollback Window   (timing check — warning only)
4. ROLLBACK     → Start rollback operations (throttled, staggered)
5. MONITOR      → Poll until complete
6. VERIFY       → Health check
7. REPORT       → Write logs + JSON report (rollback-report-YYYYMMDD-HHMMSS.json)
```

**When rollback is available:**
- Instance was recently upgraded
- A valid snapshot/previous version exists in upgrade history
- Instance is in ACTIVE state
- Within the supported rollback time window

---

## Safety Features

| Feature | Description |
|---------|-------------|
| Dry-run mode | `--dry-run` reports planned upgrades/rollbacks without performing them (still starts stopped instances to evaluate them) |
| Health checks | Verifies ACTIVE state + healthState before and after operations |
| Auto-rollback | `--rollback-on-failure` reverts failed upgrades automatically |
| Pre-checks | 4-step validation pipeline before rollback operations |
| State validation | Only operates on ACTIVE instances; auto-starts stopped ones |
| Stagger delay | Configurable delay between operations to avoid API throttling |
| Concurrency limit | `--max-parallel` caps simultaneous operations |
| Timeouts | Per-operation and health-check timeouts prevent hanging |
| Retry with backoff | Exponential backoff for transient API errors (409, 429, 5xx) |
| Structured logging | Dual output: console + log file; JSON logging in Cloud Function |
| JSON reports | Machine-readable reports with per-instance results |
| Input sanitization | Cloud Function validates and sanitizes all inputs |
| Least-privilege IAM | Terraform creates custom role with minimal permissions |

---

## Testing

### Run tests

```bash
# All tests with verbose output
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=src --cov-report=term-missing --cov-branch

# Single test file
pytest tests/unit_tests/test_rollback_prechecks.py -v
```

### Test structure

| Test file | Covers |
|-----------|--------|
| `test_cli.py` | CLI argument parsing, parser creation, all option combinations |
| `test_clients.py` | WorkbenchRestClient methods, retry logic, error handling |
| `test_config.py` | UpgraderConfig creation from args |
| `test_log_utils.py` | Logging setup, verbose mode, file handlers |
| `test_models.py` | Data model creation and field defaults |
| `test_rollback_prechecks.py` | All 4 rollback pre-check validations |

### Configuration

- **pytest.ini**: Strict markers, short tracebacks, 80% coverage threshold
- **conftest.py**: Adds `src/` to `sys.path` for direct module imports

---

## Development Setup

```bash
# Clone
git clone git@github.com:ONSdigital/wbi-fleet-upgrade.git
cd wbi-fleet-upgrade

# Python environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt

# Or install as editable package
pip install -e ".[dev]"

# Authenticate with GCP
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-project-id>

# Run tests
pytest tests/ -v

# Code quality
black src/ tests/
flake8 src/ tests/
mypy src/
pylint src/
```

---

## Troubleshooting

### Common issues

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` | Ensure `src/` is on `sys.path` — use `main.py` or install the package |
| Permission denied | Run `gcloud auth list` and verify project access with `gcloud projects describe <id>` |
| Instance busy | Wait for ongoing operations to finish (UPGRADING/STARTING/STOPPING states) |
| Rollback not available | Check upgrade history — needs a recent successful upgrade with a snapshot |
| API quota errors | Reduce `--max-parallel` and increase `--stagger-delay` |
| Timeout errors | Increase `--timeout` or `--health-check-timeout` |
| Cloud Function 415 | Ensure `Content-Type: application/json` header on POST requests |

### Log files

- `workbench-upgrade.log` — Upgrade operation logs
- `workbench-rollback.log` — Rollback operation logs
- `upgrade-report-*.json` — Structured upgrade reports
- `rollback-report-*.json` — Structured rollback reports

### Terraform note

If your Workbench instances are deployed via Terraform with environment version pinning, you must update the pinned version after upgrading. Otherwise, the next `terraform apply` will detect a version mismatch and attempt to recreate the instance.
