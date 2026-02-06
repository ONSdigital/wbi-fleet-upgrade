# =============================================================================
# WBI Fleet Upgrade Cloud Function - Terraform Configuration
# =============================================================================
# This module deploys the WBI Fleet Upgrade tool as a Google Cloud Function
# with proper IAM configuration and security controls.
#
# Usage:
#   terraform init
#   terraform plan -var="project_id=my-project"
#   terraform apply -var="project_id=my-project"
# =============================================================================

terraform {
  required_version = ">= 1.14"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.16"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.7"
    }
  }
}


# =============================================================================
# Data Sources
# =============================================================================

data "google_project" "current" {
  project_id = var.project_id
}

# =============================================================================
# Service Account
# =============================================================================

# Create service account if not provided
resource "google_service_account" "function_sa" {
  count = var.service_account_email == "" ? 1 : 0

  project      = var.project_id
  account_id   = "${var.function_name}-sa"
  display_name = "WBI Fleet Upgrade Cloud Function Service Account"
  description  = "Service account for WBI Fleet Upgrade Cloud Function with least-privilege permissions"
}

locals {
  service_account_email = var.service_account_email != "" ? var.service_account_email : google_service_account.function_sa[0].email
  target_projects       = length(var.target_project_ids) > 0 ? var.target_project_ids : [var.project_id]
}

# =============================================================================
# IAM Roles - Least Privilege
# =============================================================================

# Custom role for Workbench instance operations
resource "google_project_iam_custom_role" "workbench_operator" {
  for_each = toset(local.target_projects)

  project     = each.value
  role_id     = replace("wbi_fleet_upgrade_operator_${var.function_name}", "-", "_")
  title       = "WBI Fleet Upgrade Operator"
  description = "Least-privilege role for WBI Fleet Upgrade Cloud Function"

  permissions = [
    # Instance read operations
    "notebooks.instances.list",
    "notebooks.instances.get",
    "notebooks.instances.checkUpgradability",

    # Instance write operations
    "notebooks.instances.upgrade",
    "notebooks.instances.start",

    # Operation monitoring
    "notebooks.operations.get",
    "notebooks.operations.list",
  ]
}

resource "google_project_iam_member" "workbench_operator_binding" {
  for_each = toset(local.target_projects)

  project = each.value
  role    = google_project_iam_custom_role.workbench_operator[each.value].id
  member  = "serviceAccount:${local.service_account_email}"
}

resource "google_project_iam_member" "logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${local.service_account_email}"
}

# =============================================================================
# Cloud Function Source Code
# =============================================================================

# Archive the function source code
data "archive_file" "function_source" {
  type        = "zip"
  source_dir  = "${path.module}/../cloud_function"
  output_path = "${path.module}/function-source.zip"

  excludes = [
    ".env.yaml",
    ".env.yaml.example",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".git",
  ]
}

# Storage bucket for function source
resource "google_storage_bucket" "function_source" {
  project  = var.project_id
  name     = "${var.project_id}-${var.function_name}-source"
  location = var.region
  labels   = var.labels

  uniform_bucket_level_access = true

  force_destroy = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }
}

# Upload source code to bucket
resource "google_storage_bucket_object" "function_source" {
  name   = "source-${data.archive_file.function_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.function_source.output_path
}

# =============================================================================
# Cloud Function (Gen 2)
# =============================================================================

resource "google_cloudfunctions2_function" "wbi_upgrade" {
  project  = var.project_id
  name     = var.function_name
  location = var.region
  labels   = var.labels

  description = "WBI Fleet Upgrade & Rollback Cloud Function"

  build_config {
    runtime     = "python311"
    entry_point = "main"

    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count    = var.max_instances
    min_instance_count    = var.min_instances
    available_memory      = "${var.memory_mb}M"
    timeout_seconds       = var.timeout_seconds
    service_account_email = local.service_account_email

    environment_variables = {
      GCP_PROJECT_ID       = var.project_id
      LOCATIONS            = join(" ", var.target_locations)
      DRY_RUN              = tostring(var.dry_run_default)
      MAX_PARALLEL         = tostring(var.max_parallel_default)
      ROLLBACK_ON_FAILURE  = "false"
      APP_VERSION          = "1.0.0"
    }

    # VPC connector for private networking (optional)
    vpc_connector                  = var.vpc_connector != "" ? var.vpc_connector : null
    vpc_connector_egress_settings  = var.vpc_connector != "" ? "PRIVATE_RANGES_ONLY" : null

    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true
  }
}

# =============================================================================
# IAM - Function Invocation
# =============================================================================

# Allow specified members to invoke the function
resource "google_cloudfunctions2_function_iam_member" "invokers" {
  for_each = toset(var.allowed_invokers)

  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.wbi_upgrade.name
  role           = "roles/cloudfunctions.invoker"
  member         = each.value
}

# Allow the service account to invoke itself (for async operations)
resource "google_cloudfunctions2_function_iam_member" "self_invoke" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.wbi_upgrade.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${local.service_account_email}"
}

# =============================================================================
# Outputs
# =============================================================================

output "function_uri" {
  description = "URI of the deployed Cloud Function"
  value       = google_cloudfunctions2_function.wbi_upgrade.service_config[0].uri
}

output "function_name" {
  description = "Name of the deployed Cloud Function"
  value       = google_cloudfunctions2_function.wbi_upgrade.name
}

output "service_account_email" {
  description = "Service account email used by the Cloud Function"
  value       = local.service_account_email
}

output "source_bucket" {
  description = "Storage bucket containing the function source"
  value       = google_storage_bucket.function_source.name
}

output "invoke_command" {
  description = "Example command to invoke the function"
  value       = <<-EOT
    # Authenticate and get token
    TOKEN=$(gcloud auth print-identity-token)

    # Invoke upgrade endpoint (dry-run)
    curl -X POST "${google_cloudfunctions2_function.wbi_upgrade.service_config[0].uri}/upgrade" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"dry_run": true}' | jq

    # Invoke rollback endpoint
    curl -X POST "${google_cloudfunctions2_function.wbi_upgrade.service_config[0].uri}/rollback" \
        -H "Authorization: Bearer $TOKEN
        -H "Content-Type: application/json" \
        -d '{}' | jq

    #invoke with custom parameters
    curl -X POST "${google_cloudfunctions2_function.wbi_upgrade.service_config[0].uri}/upgrade" \
      -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
              "dry_run": false,
              "max_parallel": 10,
              "rollback_on_failure": true
            }' | jq
  EOT
}