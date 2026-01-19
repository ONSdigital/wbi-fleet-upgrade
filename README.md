# Vertex AI Workbench Fleet Upgrader

## Overview

This tool uses Google native Vertex AI Workbench Instance upgrade process and upgrades your Vertex AI Workbench instances automatically. You can upgrade entire fleets of instances across multiple locations, or target one specific instance. It also supports rolling back recently upgraded instances to their previous version.

## Quick Start

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Upgrade All Instances (Fleet Mode)

```bash
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a europe-west2-b \
  --dry-run
```

### Upgrade One Instance (Single Instance Mode)

```bash
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a \
  --instance my-notebook \
  --dry-run
```

### Roll Back All Eligible Instances (Fleet Rollback)

```bash
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a europe-west2-b \
  --rollback \
  --dry-run
```

### Roll Back One Instance (Single Instance Rollback)

```bash
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a \
  --instance my-notebook \
  --rollback
```

### Use the Bash Wrappers

```bash
# Upgrade wrapper
./wb-upgrade.sh \
  --project <project-id> \
  --locations "europe-west2-a europe-west2-b" \
  --dry-run

# Rollback wrapper
./wb-rollback.sh \
  --project <project-id> \
  --locations "europe-west2-a europe-west2-b" \
  --dry-run
```

## Main Features

- ✅ **Fleet Upgrades**: Upgrade all instances in specified locations
- ✅ **Single Instance Upgrades**: Target one specific instance
- ✅ **Parallel Processing**: Upgrade multiple instances simultaneously
- ✅ **Health Checks**: Verify instances before and after upgrading
- ✅ **Automatic Rollback on Failure**: In upgrade mode, roll back failed upgrades automatically
- ✅ **Manual Rollback Mode**: Dedicated rollback flow to revert recently upgraded instances
- ✅ **Detailed Reports**: Get comprehensive logs and JSON reports
- ✅ **Dry Run Mode**: Check what would happen without making changes
- ✅ **State Validation & Auto-Start**: Will automatically start STOPPED/SUSPENDED instances before upgrade (unless --dry-run)
- ✅ **Rollback Pre-Start**: In rollback mode, STOPPED/SUSPENDED instances are started in parallel before eligibility checks and rollback operations

## How to Use It

### Basic Commands

```bash
# Check which instances need upgrading (dry run)
python3 main.py --project <project-id> --locations LOCATION --dry-run

# Upgrade all instances in a location
python3 main.py --project <project-id> --locations LOCATION

# Upgrade one specific instance
python3 main.py --project <project-id> --locations LOCATION --instance INSTANCE_ID

# Upgrade with automatic rollback on failure
python3 main.py --project <project-id> --locations LOCATION --rollback-on-failure

# Check rollback eligibility for all instances (dry run)
python3 main.py --project <project-id> --locations LOCATION --rollback --dry-run

# Roll back one instance to previous version
python3 main.py --project <project-id> --locations LOCATION --instance INSTANCE_ID --rollback
```

### All Options

```
--project <project-id>              Your GCP project ID (required)
--locations LOC1 LOC2          Zone locations to check (required)
--instance INSTANCE_ID         Specific instance to upgrade or roll back (optional)
--rollback                     Rollback mode: revert to previous version (instead of upgrade)
--dry-run                      Check without upgrading or rolling back
--max-parallel NUM             How many to upgrade/rollback at once (default: 10)
--timeout SECONDS              How long to wait per operation (default: 7200)
--poll-interval SECONDS        How often to check progress (default: 20)
--rollback-on-failure          In upgrade mode, roll back if upgrade fails
--health-check-timeout SECS    How long to wait for health check (default: 800)
--stagger-delay SECONDS        Delay between starting operations (default: 5.0)
--verbose                      Show detailed output
```

## Project Structure

```
/
├── fleet_upgrader/         Main Python package
│   ├── clients.py          API client for Workbench
│   ├── upgrader.py         Upgrade logic
│   ├── rollback.py         Rollback logic
│   ├── models.py           Data structures
│   ├── config.py           Configuration
│   └── log_utils.py        Logging setup
├── main.py                 Python CLI tool (upgrade & rollback)
├── wb-upgrade.sh           Bash wrapper for upgrades
├── wb-rollback.sh          Bash wrapper for rollback
└── tests/                  Unit tests
```

## Real Examples

### Example 1: Test Before Upgrading

```bash
# Always test with --dry-run first
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a \
  --dry-run
```

### Example 2: Upgrade Multiple Locations

```bash
# Upgrade all instances in three locations
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a europe-west2-b europe-west2-c \
  --max-parallel 10
```

### Example 3: Upgrade One Critical Instance

```bash
# Upgrade one instance with rollback protection
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a \
  --instance wbi \
  --rollback-on-failure \
  --verbose
```

### Example 4: Use Environment Variables

```bash
export GCP_PROJECT_ID=<project-id>
export LOCATIONS="europe-west2-a europe-west2-b"

./wb-upgrade.sh --dry-run
```

### Example 5: Check Rollback Eligibility (Fleet)

```bash
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a europe-west2-b \
  --rollback \
  --dry-run
```

### Example 6: Roll Back One Instance

```bash
python3 main.py \
  --project <project-id> \
  --locations europe-west2-a \
  --instance wbi \
  --rollback \
  --verbose
```

## Safety Features

1. **Dry Run Mode**: Test everything before making changes
2. **Health Checks**: Verify instances are ready before and after
3. **Automatic Rollback**: In upgrade mode, undo failed upgrades automatically
4. **Dedicated Rollback Mode**: Revert recently upgraded instances when supported
5. **Detailed Logging**: Track every step in `workbench-upgrade.log` and `workbench-rollback.log`
6. **JSON Reports**: Get structured reports in `upgrade-report-*.json` and `rollback-report-*.json`
7. **State Validation**: Only operate on ACTIVE instances

## What Happens During an Upgrade

1. **Scan**: Find all instances in specified locations
2. **Pre-Start (Fleet)**: Start all STOPPED/SUSPENDED instances in parallel; skip actual start in --dry-run
3. **Check**: Verify each instance is ACTIVE and ready; if STOPPED/SUSPENDED and not yet started, the tool will start it automatically
4. **Test Upgradeability**: Ask GCP if upgrade is available
5. **Upgrade**: Start the upgrade operation
6. **Monitor**: Poll the operation until complete
7. **Verify**: Check instance is ACTIVE and healthy
8. **Report**: Generate detailed logs and reports

## Rollback Guide

### When You Can Roll Back

Rollback is only available if the instance was upgraded recently and a valid snapshot/previous version exists. The tool performs pre-checks to confirm:

- Instance is in ACTIVE state (STOPPED/SUSPENDED instances are started in parallel first, unless --dry-run)
- Recent successful upgrade is present in history
- A valid snapshot/rollback target exists
- Rollback timing window is still valid

Use `--rollback --dry-run` to check eligibility safely.

### How Rollback Works

1. **Scan**: Find instances in specified locations (or a single instance)
2. **Pre-Start (Fleet)**: Start all STOPPED/SUSPENDED instances in parallel prior to eligibility checks; skip actual start in --dry-run
3. **Pre-Checks**: Validate state, upgrade history, snapshot, and timing window
4. **Rollback**: Trigger rollback to the previous version
5. **Monitor**: Poll until the operation completes
6. **Verify**: Confirm the instance returns to ACTIVE and is healthy
7. **Report**: Write logs to `workbench-rollback.log` and JSON report `rollback-report-*.json`

### Rollback Commands

```bash
# Fleet rollback eligibility (dry run) - also shows which STOPPED/SUSPENDED instances would be started
python3 main.py --project <project-id> --locations LOCATIONS --rollback --dry-run

# Single instance rollback (auto-start if STOPPED/SUSPENDED)
python3 main.py --project <project-id> --locations LOCATIONS --instance INSTANCE_ID --rollback

# Bash wrapper (with env vars)
export GCP_PROJECT_ID=<project-id>
export LOCATIONS="europe-west2-a europe-west2-b"
./wb-rollback.sh --dry-run
```

### Rollback Troubleshooting

**Rollback Not Available**

```bash
# Check upgrade history for the instance
# Ensure a recent successful upgrade exists
# Confirm snapshot/previous version is available
```

**Permission Denied**

```bash
# Check you're logged in
gcloud auth list

# Check project access
gcloud projects describe PROJECT_ID
```

**Instance Busy or Not Running**

```bash
# Ensure instance is ACTIVE
# Wait for ongoing operations to finish (UPGRADING/STARTING/STOPPING)
```

## Development

### Run Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=fleet_upgrader --cov-report=html
```

## Requirements

- Python 3.8 or newer
- Google Cloud SDK (`gcloud`)
- Active GCP authentication
- Notebooks API enabled: [Notebooks API (v2)](https://notebooks.googleapis.com/$discovery/rest?version=v2)
- Required Python packages (see `requirements.txt`)

## Support

- File issues on GitHub
- Check logs in `workbench-upgrade.log` and `workbench-rollback.log`
- Review JSON reports for detailed information
- Use `--verbose` for debugging
