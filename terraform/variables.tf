
# =============================================================================
# Variables
# =============================================================================

variable "project_id" {
  description = "GCP project ID where the Cloud Function will be deployed"
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud Function"
  type        = string
  default     = "europe-west2"
}

variable "function_name" {
  description = "Name of the Cloud Function"
  type        = string
  default     = "wbi-fleet-upgrade"
}

variable "target_project_ids" {
  description = "List of project IDs where Workbench instances are managed (defaults to deploying project)"
  type        = list(string)
  default     = []
}

variable "target_locations" {
  description = "Default GCP zones to scan for instances"
  type        = list(string)
  default     = ["europe-west2-a", "europe-west2-b", "europe-west2-c"]
}

variable "dry_run_default" {
  description = "Default value for dry_run mode (true = safer)"
  type        = bool
  default     = true
}

variable "max_parallel_default" {
  description = "Default maximum parallel operations"
  type        = number
  default     = 5
}

variable "timeout_seconds" {
  description = "Cloud Function timeout in seconds (max 540 for HTTP, 3600 for event-driven)"
  type        = number
  default     = 540
}

variable "memory_mb" {
  description = "Memory allocation for the Cloud Function in MB"
  type        = number
  default     = 512
}

variable "min_instances" {
  description = "Minimum number of instances (0 = cold start allowed)"
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum number of instances"
  type        = number
  default     = 10
}

variable "vpc_connector" {
  description = "VPC connector name for private networking (optional)"
  type        = string
  default     = ""
}

variable "service_account_email" {
  description = "Service account email to use (if empty, a new one is created)"
  type        = string
  default     = ""
}

variable "allowed_invokers" {
  description = "List of IAM members allowed to invoke the function (e.g., 'user:admin@example.com')"
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels to apply to resources"
  type        = map(string)
  default = {
    managed-by = "terraform"
    component  = "wbi-fleet-upgrade"
  }
}