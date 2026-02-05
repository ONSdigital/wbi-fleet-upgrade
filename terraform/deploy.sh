#!/usr/bin/env bash
#
# WBI Fleet Upgrade Cloud Function - Deployment Script
#
# This script handles all prerequisites and deploys the Cloud Function via Terraform.
#
# Usage:
#   ./deploy.sh                    # Interactive mode
#   ./deploy.sh --auto-approve     # Non-interactive mode
#   ./deploy.sh --plan-only        # Only run terraform plan
#   ./deploy.sh --destroy          # Destroy the deployment
#

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TFVARS_FILE="${SCRIPT_DIR}/terraform.tfvars"
TFVARS_EXAMPLE="${SCRIPT_DIR}/terraform.tfvars.example"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

check_command() {
  if command -v "$1" &>/dev/null; then
    log_success "$1 is installed"
    return 0
  else
    log_error "$1 is not installed"
    return 1
  fi
}

# =============================================================================
# Prerequisite Checks
# =============================================================================

check_prerequisites() {
  log_info "Checking prerequisites..."
  echo ""

  local failed=0

  # Check required commands
  check_command "gcloud" || failed=1
  check_command "terraform" || failed=1
  check_command "jq" || log_warn "jq not installed (optional, for JSON parsing)"

  echo ""

  # Check Terraform version
  if command -v terraform &>/dev/null; then
    local tf_version
    tf_version=$(terraform version -json 2>/dev/null | jq -r '.terraform_version' 2>/dev/null || terraform version | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
    local tf_major tf_minor
    tf_major=$(echo "$tf_version" | cut -d. -f1)
    tf_minor=$(echo "$tf_version" | cut -d. -f2)

    if [[ "$tf_major" -gt 1 ]] || [[ "$tf_major" -eq 1 && "$tf_minor" -ge 14 ]]; then
      log_success "Terraform version $tf_version (>= 1.14 required)"
    else
      log_error "Terraform version $tf_version is too old (>= 1.14 required)"
      failed=1
    fi
  fi

  # Check gcloud authentication
  if gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | grep -q "@"; then
    local account
    account=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
    log_success "gcloud authenticated as: $account"
  else
    log_error "gcloud is not authenticated. Run: gcloud auth login"
    failed=1
  fi

  # Check application default credentials
  if gcloud auth application-default print-access-token &>/dev/null; then
    log_success "Application default credentials configured"
  else
    log_warn "Application default credentials not set. Run: gcloud auth application-default login"
  fi

  echo ""

  if [[ $failed -ne 0 ]]; then
    log_error "Prerequisites check failed. Please fix the issues above."
    exit 1
  fi

  log_success "All prerequisites satisfied"
}

# =============================================================================
# Configuration Setup
# =============================================================================

setup_configuration() {
  log_info "Checking Terraform configuration..."
  echo ""

  # Check if terraform.tfvars exists
  if [[ ! -f "$TFVARS_FILE" ]]; then
    if [[ -f "$TFVARS_EXAMPLE" ]]; then
      log_warn "terraform.tfvars not found. Creating from example..."
      cp "$TFVARS_EXAMPLE" "$TFVARS_FILE"
      log_info "Please edit ${TFVARS_FILE} with your configuration"
      echo ""
      read -rp "Press Enter to open the file in your editor, or Ctrl+C to exit..."
      ${EDITOR:-vi} "$TFVARS_FILE"
    else
      log_error "Neither terraform.tfvars nor terraform.tfvars.example found"
      exit 1
    fi
  else
    log_success "terraform.tfvars found"
  fi

  # Extract project_id from tfvars (handles spaces, quotes, and equals signs)
  PROJECT_ID=$(grep -E '^project_id\s*=' "$TFVARS_FILE" | head -1 | cut -d'=' -f2 | tr -d ' "')

  if [[ -z "$PROJECT_ID" ]]; then
    log_error "project_id not found in terraform.tfvars"
    exit 1
  fi

  log_success "Project ID: $PROJECT_ID"

  # Extract region
  REGION=$(grep -E '^region\s*=' "$TFVARS_FILE" | head -1 | sed 's/^region\s*=\s*"\([^"]*\)".*/\1/')
  REGION=${REGION:-"europe-west2"}
  log_success "Region: $REGION"

  echo ""
}

# =============================================================================
# GCP API Enablement
# =============================================================================

enable_apis() {
  log_info "Checking and enabling required GCP APIs..."
  echo ""

  local apis=(
    "cloudfunctions.googleapis.com"
    "cloudbuild.googleapis.com"
    "artifactregistry.googleapis.com"
    "run.googleapis.com"
    "storage.googleapis.com"
    "notebooks.googleapis.com"
    "iam.googleapis.com"
    "cloudresourcemanager.googleapis.com"
  )

  # Optimization: Get all enabled APIs at once to reduce calls
  local enabled_apis
  enabled_apis=$(gcloud services list --project="$PROJECT_ID" --enabled --format="value(config.name)" 2>/dev/null || echo "")

  for api in "${apis[@]}"; do
    if echo "$enabled_apis" | grep -q "^$api$"; then
      log_success "$api is already enabled"
    else
      log_info "Enabling $api..."
      # Capture error output so we can see WHY it fails (e.g., Billing or Permissions)
      local error_msg
      if error_msg=$(gcloud services enable "$api" --project="$PROJECT_ID" 2>&1); then
        log_success "$api enabled"
      else
        log_error "Failed to enable $api"
        echo -e "${YELLOW}Reason:${NC} $error_msg"
        exit 1
      fi
    fi
  done

  echo ""
  log_success "All required APIs are enabled"
}

# =============================================================================
# Terraform Operations
# =============================================================================

terraform_init() {
  log_info "Initializing Terraform..."
  echo ""

  cd "$SCRIPT_DIR"

  if terraform init -upgrade; then
    log_success "Terraform initialized"
  else
    log_error "Terraform init failed"
    exit 1
  fi

  echo ""
}

terraform_validate() {
  log_info "Validating Terraform configuration..."
  echo ""

  cd "$SCRIPT_DIR"

  if terraform validate; then
    log_success "Terraform configuration is valid"
  else
    log_error "Terraform validation failed"
    exit 1
  fi

  echo ""
}

terraform_plan() {
  log_info "Running Terraform plan..."
  echo ""

  cd "$SCRIPT_DIR"

  if terraform plan -out=tfplan; then
    log_success "Terraform plan completed"
  else
    log_error "Terraform plan failed"
    exit 1
  fi

  echo ""
}

terraform_apply() {
  local auto_approve=$1

  log_info "Applying Terraform configuration..."
  echo ""

  cd "$SCRIPT_DIR"

  local apply_args=("-auto-approve")
  if [[ "$auto_approve" != "true" ]]; then
    apply_args=()
  fi

  if [[ -f "tfplan" ]]; then
    if terraform apply "${apply_args[@]}" tfplan; then
      log_success "Terraform apply completed"
      rm -f tfplan
    else
      log_error "Terraform apply failed"
      exit 1
    fi
  else
    if terraform apply "${apply_args[@]}"; then
      log_success "Terraform apply completed"
    else
      log_error "Terraform apply failed"
      exit 1
    fi
  fi

  echo ""
}

terraform_destroy() {
  local auto_approve=$1

  log_warn "Destroying Terraform resources..."
  echo ""

  cd "$SCRIPT_DIR"

  # Use a standard string instead of an array for simpler flag handling
  local destroy_args=""
  if [[ "$auto_approve" == "true" ]]; then
    destroy_args="-auto-approve"
  fi

  # Pass the variable; if empty, it just adds nothing to the command
  if terraform destroy $destroy_args; then
    log_success "Terraform destroy completed"
  else
    log_error "Terraform destroy failed"
    exit 1
  fi

  echo ""
}

# =============================================================================
# Post-Deployment
# =============================================================================

show_outputs() {
  log_info "Deployment outputs:"
  echo ""

  cd "$SCRIPT_DIR"

  local function_uri
  function_uri=$(terraform output -raw function_uri 2>/dev/null || echo "")

  if [[ -n "$function_uri" ]]; then
    TOKEN=$(gcloud auth print-identity-token)
    URL="$function_uri"

    echo -e "${GREEN}Function URL:${NC} $function_uri"
    echo ""
    echo -e "${BLUE}Test commands:${NC}"
    echo ""
    echo "# Get authentication token"
    echo "TOKEN=\$(gcloud auth print-identity-token)"
    echo ""
    echo "# Set URL"
    echo "URL=\"$function_uri\""
    echo ""
    echo "# Check API info"
    echo "curl -H \"Authorization: Bearer \$TOKEN\" \"$function_uri/\""
    echo ""
    echo "# Check instance status"
    echo "curl -H \"Authorization: Bearer \$TOKEN\" \"$function_uri/status\""
    echo ""
    echo "# Check upgradability (dry-run)"
    echo "curl -X POST \"$function_uri/check-upgradability\" \\"
    echo "  -H \"Authorization: Bearer \$TOKEN\" \\"
    echo "  -H \"Content-Type: application/json\" \\"
    echo "  -d '{\"dry_run\": true}'"
    echo ""
  fi
}

# =============================================================================
# Main
# =============================================================================

main() {
  local auto_approve="false"
  local plan_only="false"
  local destroy="false"

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case $1 in
    --auto-approve | -y)
      auto_approve="true"
      shift
      ;;
    --plan-only | --plan)
      plan_only="true"
      shift
      ;;
    --destroy)
      destroy="true"
      shift
      ;;
    --help | -h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --auto-approve, -y   Skip confirmation prompts"
      echo "  --plan-only, --plan  Only run terraform plan"
      echo "  --destroy            Destroy the deployment"
      echo "  --help, -h           Show this help message"
      exit 0
      ;;
    *)
      log_error "Unknown option: $1"
      exit 1
      ;;
    esac
  done

  echo ""
  echo "=========================================="
  echo "WBI Fleet Upgrade Cloud Function Deployment"
  echo "=========================================="
  echo ""

  # Run checks and setup
  check_prerequisites
  echo ""

  setup_configuration
  echo ""

  enable_apis
  echo ""

  # Terraform operations
  terraform_init
  terraform_validate

  if [[ "$destroy" == "true" ]]; then
    terraform_destroy "$auto_approve"
    log_success "Deployment destroyed successfully"
    exit 0
  fi

  terraform_plan

  if [[ "$plan_only" == "true" ]]; then
    log_info "Plan-only mode. Skipping apply."
    exit 0
  fi

  # Confirm before apply if not auto-approve
  if [[ "$auto_approve" != "true" ]]; then
    echo ""
    read -rp "Do you want to apply this plan? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
      log_info "Deployment cancelled"
      exit 0
    fi
  fi

  terraform_apply "$auto_approve"

  echo ""
  echo "=========================================="
  log_success "Deployment completed successfully!"
  echo "=========================================="
  echo ""

  show_outputs
}

# Run main
main "$@"