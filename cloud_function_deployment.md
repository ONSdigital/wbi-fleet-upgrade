# WBI Fleet Upgrade - Cloud Function Deployment Guide

This guide covers deploying the WBI Fleet Upgrade tool as a Google Cloud Function.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [IAM & Security](#iam--security)
- [Terraform Deployment](#terraform-deployment)
- [Manual Deployment](#manual-deployment)
- [Usage Examples](#usage-examples)
- [Monitoring & Logging](#monitoring--logging)
- [Troubleshooting](#troubleshooting)

## Overview

The Cloud Function deployment provides:

- **REST API** for upgrade and rollback operations
- **Serverless execution** with automatic scaling
- **IAM authentication** for secure access
- **JSON structured logging** for Cloud Logging integration
- **Environment-based configuration** for flexibility

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API information |
| `/upgrade` | POST | Upgrade Workbench instances |
| `/rollback` | POST | Rollback Workbench instances |
| `/status` | GET/POST | Get instance status |
| `/check-upgradability` | GET/POST | Check which instances can be upgraded |
| `/health` | GET | Health check |

## Prerequisites

1. **GCP Project** with billing enabled
2. **APIs enabled:**
   ```bash
   gcloud services enable \
     cloudfunctions.googleapis.com \
     cloudbuild.googleapis.com \
     notebooks.googleapis.com \
     storage.googleapis.com
   ```
3. **Terraform** (>= 1.14) for infrastructure deployment
4. **gcloud CLI** authenticated with appropriate permissions

## Quick Start

### 1. Clone and Navigate

```bash
cd wbi-fleet-upgrade/terraform/cloud-function
```

### 2. Configure Variables

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### 3. Deploy

```bash
terraform init
terraform plan
terraform apply
```

### 4. Test

```bash
# Get the function URL
FUNCTION_URL=$(terraform output -raw function_uri)

# Get authentication token
TOKEN=$(gcloud auth print-identity-token)

# Check API info
curl -H "Authorization: Bearer $TOKEN" "$FUNCTION_URL/"

# Run dry-run upgrade check
curl -X POST "$FUNCTION_URL/upgrade" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

## API Reference

### POST /upgrade

Upgrade Workbench instances.

**Request Body:**

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

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| project_id | string | Yes* | env var | GCP project ID |
| locations | string[] | Yes* | env var | Zone list |
| instance_id | string | No | null | Single instance mode |
| dry_run | boolean | No | true | Simulation mode |
| max_parallel | integer | No | 5 | Max concurrent ops |
| timeout | integer | No | 7200 | Operation timeout (s) |
| rollback_on_failure | boolean | No | false | Auto-rollback |
| health_check_timeout | integer | No | 600 | Health check timeout (s) |
| stagger_delay | float | No | 3.0 | Delay between ops (s) |

*Can be provided via environment variables instead.

**Response:**

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
    "results": [
      {
        "instance_name": "notebook-1",
        "location": "europe-west2-a",
        "status": "dry_run",
        "target_version": "M123",
        "duration_seconds": null,
        "error_message": null,
        "rolled_back": false
      }
    ]
  }
}
```

### POST /rollback

Rollback Workbench instances to previous version.

**Request Body:** Same as `/upgrade` (excluding `rollback_on_failure`)

### GET/POST /status

Get current state of instances.

**Request/Query Parameters:**

```json
{
  "project_id": "my-project",
  "locations": ["europe-west2-a"],
  "instance_id": "my-instance"
}
```

### GET/POST /check-upgradability

Check which instances are upgradeable.

**Response:**

```json
{
  "success": true,
  "data": {
    "project_id": "my-project",
    "locations": ["europe-west2-a"],
    "total_instances": 10,
    "upgradeable_count": 3,
    "instances": [
      {
        "name": "notebook-1",
        "location": "europe-west2-a",
        "upgradeable": true,
        "target_version": "M123"
      }
    ]
  }
}
```

### GET /health

Health check endpoint.

```json
{
  "success": true,
  "data": {
    "status": "healthy"
  }
}
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| GCP_PROJECT_ID | Yes* | - | Default project ID |
| LOCATIONS | Yes* | - | Space-separated zones |
| DRY_RUN | No | true | Default dry-run mode |
| MAX_PARALLEL | No | 5 | Default parallel ops |
| ROLLBACK_ON_FAILURE | No | false | Default rollback behavior |
| TIMEOUT | No | 7200 | Default timeout |
| HEALTH_CHECK_TIMEOUT | No | 600 | Default health timeout |
| POLL_INTERVAL | No | 20 | Status poll interval |
| STAGGER_DELAY | No | 3.0 | Delay between ops |
| WBI_API_KEY | No | - | Additional auth key |
| APP_VERSION | No | 1.0.0 | Version for logging |

*Can be provided per-request instead.

### Example .env.yaml

```yaml
GCP_PROJECT_ID: "my-project"
LOCATIONS: "europe-west2-a europe-west2-b"
DRY_RUN: "true"
MAX_PARALLEL: "5"
```

## IAM & Security

### Least Privilege Permissions

The Cloud Function requires these permissions:

```yaml
# Workbench Instance Operations
notebooks.instances.list
notebooks.instances.get
notebooks.instances.checkUpgradability
notebooks.instances.upgrade
notebooks.instances.rollback
notebooks.instances.start

# Operation Monitoring
notebooks.operations.get
notebooks.operations.list
```

### Custom IAM Role

The Terraform module creates a custom role with exactly these permissions:

```hcl
resource "google_project_iam_custom_role" "workbench_operator" {
  role_id     = "wbi_fleet_upgrade_operator"
  title       = "WBI Fleet Upgrade Operator"
  permissions = [
    "notebooks.instances.list",
    "notebooks.instances.get",
    "notebooks.instances.checkUpgradability",
    "notebooks.instances.upgrade",
    "notebooks.instances.rollback",
    "notebooks.instances.start",
    "notebooks.operations.get",
    "notebooks.operations.list",
  ]
}
```

### Authentication Layers

1. **Cloud Functions IAM** (primary): Only authorized principals can invoke
2. **API Key** (optional): Additional application-level key via `X-API-Key` header
3. **Input Validation**: All inputs are sanitized and validated

### Security Best Practices

1. **Use IAM for invocation control:**
   ```bash
   gcloud functions add-invoker-policy-binding wbi-fleet-upgrade \
     --member="user:admin@example.com" \
     --region=europe-west2
   ```

2. **Enable VPC connector for private access:**
   ```hcl
   vpc_connector = "projects/my-project/locations/europe-west2/connectors/my-connector"
   ```

3. **Enable audit logging:**
   ```bash
   gcloud logging sinks create wbi-audit-sink \
     storage.googleapis.com/my-audit-bucket \
     --log-filter='resource.type="cloud_function" AND resource.labels.function_name="wbi-fleet-upgrade"'
   ```

## Terraform Deployment

### Directory Structure

```
terraform/cloud-function/
├── main.tf                    # Main configuration
├── variables.tf               # Variable definitions
├── terraform.tfvars.example   # Example values
└── README.md                  # Module documentation
```

### Deployment Steps

```bash
# 1. Initialize
cd terraform/cloud-function
terraform init

# 2. Configure
cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

# 3. Plan
terraform plan -out=tfplan

# 4. Apply
terraform apply tfplan

# 5. Get outputs
terraform output function_uri
terraform output invoke_command
```

### Cross-Project Deployment

To manage instances in multiple projects:

```hcl
target_project_ids = [
  "project-a",
  "project-b",
  "project-c"
]
```

The Terraform module will create IAM bindings in each project.

## Manual Deployment

If not using Terraform:

```bash
# 1. Create service account
gcloud iam service-accounts create wbi-fleet-upgrade-sa \
  --display-name="WBI Fleet Upgrade"

# 2. Grant permissions
gcloud projects add-iam-policy-binding my-project \
  --member="serviceAccount:wbi-fleet-upgrade-sa@my-project.iam.gserviceaccount.com" \
  --role="roles/notebooks.admin"

# 3. Deploy function
cd cloud_function
gcloud functions deploy wbi-fleet-upgrade \
  --gen2 \
  --runtime=python311 \
  --region=europe-west2 \
  --source=. \
  --entry-point=main \
  --trigger-http \
  --memory=512MB \
  --timeout=540s \
  --service-account=wbi-fleet-upgrade-sa@my-project.iam.gserviceaccount.com \
  --set-env-vars="GCP_PROJECT_ID=my-project,LOCATIONS=europe-west2-a europe-west2-b,DRY_RUN=true"

# 4. Set invoker permissions
gcloud functions add-invoker-policy-binding wbi-fleet-upgrade \
  --region=europe-west2 \
  --member="user:admin@example.com"
```

## Usage Examples

### Python Client

```python
import requests
import google.auth
import google.auth.transport.requests

# Get authentication token
creds, project = google.auth.default()
auth_req = google.auth.transport.requests.Request()
creds.refresh(auth_req)
token = creds.token

FUNCTION_URL = "https://europe-west2-my-project.cloudfunctions.net/wbi-fleet-upgrade"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Check upgradability
response = requests.get(
    f"{FUNCTION_URL}/check-upgradability",
    headers=headers,
    params={"project_id": "my-project", "locations": "europe-west2-a"}
)
print(response.json())

# Dry-run upgrade
response = requests.post(
    f"{FUNCTION_URL}/upgrade",
    headers=headers,
    json={
        "project_id": "my-project",
        "locations": ["europe-west2-a"],
        "dry_run": True
    }
)
print(response.json())

# Actual upgrade (be careful!)
response = requests.post(
    f"{FUNCTION_URL}/upgrade",
    headers=headers,
    json={
        "project_id": "my-project",
        "locations": ["europe-west2-a"],
        "instance_id": "my-notebook",
        "dry_run": False,
        "rollback_on_failure": True
    }
)
print(response.json())
```

### cURL Examples

```bash
# Set variables
FUNCTION_URL="https://europe-west2-my-project.cloudfunctions.net/wbi-fleet-upgrade"
TOKEN=$(gcloud auth print-identity-token)

# 1. Check API status
curl -s "$FUNCTION_URL/" -H "Authorization: Bearer $TOKEN" | jq

# 2. List all instances
curl -s "$FUNCTION_URL/status" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "my-project", "locations": ["europe-west2-a"]}' | jq

# 3. Check upgradability
curl -s "$FUNCTION_URL/check-upgradability" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "my-project", "locations": ["europe-west2-a"]}' | jq

# 4. Dry-run fleet upgrade
curl -s -X POST "$FUNCTION_URL/upgrade" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-project",
    "locations": ["europe-west2-a", "europe-west2-b"],
    "dry_run": true
  }' | jq

# 5. Upgrade single instance (actual)
curl -s -X POST "$FUNCTION_URL/upgrade" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-project",
    "locations": ["europe-west2-a"],
    "instance_id": "my-notebook",
    "dry_run": false,
    "rollback_on_failure": true
  }' | jq

# 6. Check rollback eligibility
curl -s -X POST "$FUNCTION_URL/rollback" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-project",
    "locations": ["europe-west2-a"],
    "instance_id": "my-notebook",
    "dry_run": true
  }' | jq
```

### Cloud Scheduler Integration

Automate regular checks:

```bash
# Create scheduler job for weekly upgrade check
gcloud scheduler jobs create http wbi-upgrade-check \
  --schedule="0 6 * * 1" \
  --uri="https://europe-west2-my-project.cloudfunctions.net/wbi-fleet-upgrade/check-upgradability" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"project_id":"my-project","locations":["europe-west2-a"]}' \
  --oidc-service-account-email="scheduler@my-project.iam.gserviceaccount.com" \
  --location=europe-west2
```

## Monitoring & Logging

### View Logs

```bash
# Recent logs
gcloud functions logs read wbi-fleet-upgrade --region=europe-west2 --limit=50

# Filter by severity
gcloud logging read 'resource.type="cloud_function" AND resource.labels.function_name="wbi-fleet-upgrade" AND severity>=WARNING' --limit=100
```

### Log Query (Cloud Console)

```
resource.type="cloud_function"
resource.labels.function_name="wbi-fleet-upgrade"
jsonPayload.message=~"COMPLETED|FAILED"
```

### Alerting

Create an alert for failures:

```bash
gcloud alpha monitoring policies create \
  --notification-channels="projects/my-project/notificationChannels/123" \
  --display-name="WBI Upgrade Failures" \
  --condition-display-name="Error rate > 5%" \
  --condition-filter='resource.type="cloud_function" AND resource.labels.function_name="wbi-fleet-upgrade" AND metric.type="cloudfunctions.googleapis.com/function/execution_count" AND metric.labels.status!="ok"' \
  --condition-threshold-value=5 \
  --condition-threshold-comparison=COMPARISON_GT
```

## Troubleshooting

### Common Issues

**1. Permission Denied (403)**
```
Error: Permission 'notebooks.instances.list' denied
```
Solution: Verify IAM bindings are correctly applied:
```bash
gcloud projects get-iam-policy my-project \
  --flatten="bindings[].members" \
  --filter="bindings.members:wbi-fleet-upgrade-sa"
```

**2. Function Timeout**
```
Error: Function execution took X ms, exceeding maximum timeout
```
Solution: For long operations, consider:
- Increase timeout (max 540s for HTTP)
- Use async invocation with Pub/Sub trigger
- Process smaller batches

**3. Cold Start Latency**
Solution: Set `min_instances = 1` to keep one instance warm:
```hcl
min_instances = 1
```

**4. VPC Connectivity Issues**
```
Error: Could not connect to notebooks.googleapis.com
```
Solution: Ensure VPC connector has route to Google APIs:
```bash
gcloud compute networks subnets update my-subnet \
  --region=europe-west2 \
  --enable-private-ip-google-access
```

### Debug Mode

Enable verbose logging:
```bash
# Add to environment variables
VERBOSE: "true"
```

### Health Check

```bash
curl -s "$FUNCTION_URL/health" -H "Authorization: Bearer $TOKEN"
# Response: {"success": true, "data": {"status": "healthy"}}
```