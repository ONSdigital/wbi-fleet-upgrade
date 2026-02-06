#!/bin/bash

# shellcheck disable=SC2206

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="$(basename "$0")"

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-}"
LOCATIONS="${LOCATIONS:-}"
INSTANCE_ID="${INSTANCE_ID:-}"
DRY_RUN="${DRY_RUN:-false}"
MAX_PARALLEL="${MAX_PARALLEL:-5}"
TIMEOUT="${TIMEOUT:-3600}"
POLL_INTERVAL="${POLL_INTERVAL:-20}"
HEALTH_CHECK_TIMEOUT="${HEALTH_CHECK_TIMEOUT:-600}"
STAGGER_DELAY="${STAGGER_DELAY:-3.0}"
VERBOSE="${VERBOSE:-false}"

# Python environment
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/.venv}"
PYTHON_CMD="${PYTHON_CMD:-}"
PYTHON_SCRIPT_ROLLBACK="$SCRIPT_DIR/main.py"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
USE_VENV="${USE_VENV:-true}"
SKIP_VENV_CHECK="${SKIP_VENV_CHECK:-false}"
REQUIRED_PACKAGES=("google-auth" "google-auth-httplib2" "requests")

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_debug() {
	[[ "$VERBOSE" == "true" ]] && echo -e "${CYAN}[DEBUG]${NC} $1"
	return 0
}
log_step() { echo -e "${BLUE}${BOLD}==> $1${NC}"; }

check_command() {
	command -v "$1" &>/dev/null || {
		log_error "$1 is not installed or not in PATH"
		return 1
	}
}

print_banner() {
	local mode="Fleet"
	[[ -n "$INSTANCE_ID" ]] && mode="Single Instance"

	echo ""
	echo -e "${BOLD}========================================================"
	echo " Vertex AI Workbench $mode Rollback Runner"
	echo "========================================================${NC}"
	echo " Project:              $PROJECT_ID"
	echo " Locations:            $LOCATIONS"
	[[ -n "$INSTANCE_ID" ]] && echo " Instance:             $INSTANCE_ID"
	echo " Dry Run:              $DRY_RUN"
	echo " Max Parallel:         $MAX_PARALLEL"
	echo " Timeout:              ${TIMEOUT}s"
	echo " Poll Interval:        ${POLL_INTERVAL}s"
	echo " Health Check Timeout: ${HEALTH_CHECK_TIMEOUT}s"
	echo " Stagger Delay:        ${STAGGER_DELAY}s"
	echo " Python venv:          $VENV_DIR"
	echo "========================================================"
	echo ""
}

show_usage() {
	cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Roll back Vertex AI Workbench instances to their previous version.

⚠️  IMPORTANT: Rollback is only available if you upgraded within the supported window.
    See: https://docs.cloud.google.com/vertex-ai/docs/workbench/instances/upgrade

Options:
    --project PROJECT_ID          GCP project ID (required)
    --locations "LOC1 LOC2 ..."   Space-separated zones (required)
    --instance INSTANCE_ID        Specific instance to roll back (single instance mode)
    --dry-run                     Check rollback eligibility without rolling back
    --max-parallel NUM            Maximum parallel rollbacks (default: 5)
    --timeout SECONDS             Timeout per rollback operation (default: 3600)
    --poll-interval SECONDS       Seconds between status polls (default: 20)
    --health-check-timeout SECS   Timeout waiting for ACTIVE state (default: 600)
    --stagger-delay SECONDS       Delay between starting rollbacks (default: 3.0)
    --verbose, -v                 Enable verbose output
    --no-venv                     Don't use virtual environment
    --venv-dir PATH               Custom virtual environment path
    --python PATH                 Path to Python interpreter
    --skip-venv-check             Skip venv validation (faster startup)
    --help, -h                    Show this help message

Examples:
    # Fleet mode - check all instances for rollback eligibility
    $SCRIPT_NAME --project my-project --locations europe-west2-a --dry-run

    # Single instance mode - roll back specific instance
    $SCRIPT_NAME --project my-project --locations europe-west2-a \
        --instance my-notebook-instance

    # Fleet mode - roll back all eligible instances
    $SCRIPT_NAME --project my-project \
        --locations "europe-west2-a europe-west2-b europe-west2-c" \
        --max-parallel 3

    # Using environment variables
    export GCP_PROJECT_ID=my-project
    export LOCATIONS="europe-west2-a europe-west2-b"
    export INSTANCE_ID=my-instance
    $SCRIPT_NAME --dry-run

⚠️  ROLLBACK PREREQUISITES:
    - Instance must have been upgraded recently (within rollback window)
    - Instance must be in ACTIVE state
    - Previous version must still be available
    - No additional upgrades or modifications since last upgrade

    Check rollback eligibility first with --dry-run before proceeding.
EOF
}

find_python() {
	local candidates=("python3.12" "python3.11" "python3.10" "python3.9" "python3" "python")

	if [[ -n "$PYTHON_CMD" ]]; then
		command -v "$PYTHON_CMD" &>/dev/null && {
			echo "$PYTHON_CMD"
			return 0
		}
		log_error "Specified Python not found: $PYTHON_CMD"
		return 1
	fi

	for cmd in "${candidates[@]}"; do
		if command -v "$cmd" &>/dev/null; then
			local version major minor
			version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
			major=$(echo "$version" | cut -d. -f1)
			minor=$(echo "$version" | cut -d. -f2)

			if [[ "$major" -ge 3 && "$minor" -ge 8 ]]; then
				log_debug "Found suitable Python: $cmd (version $version)"
				echo "$cmd"
				return 0
			fi
		fi
	done

	log_error "No suitable Python interpreter found (need Python >= 3.8)"
	return 1
}

setup_venv() {
	local python_cmd="$1"

	if [[ "$USE_VENV" != "true" ]]; then
		log_info "Skipping virtual environment (--no-venv)"
		PYTHON_CMD="$python_cmd"
		return 0
	fi

	log_step "Setting up Python virtual environment"

	if [[ -d "$VENV_DIR" && -f "$VENV_DIR/bin/activate" ]]; then
		if [[ "$SKIP_VENV_CHECK" == "true" ]] || "$VENV_DIR/bin/python" -c "import sys" &>/dev/null; then
			log_info "Using existing venv: $VENV_DIR"
			PYTHON_CMD="$VENV_DIR/bin/python"
			return 0
		fi
		log_warn "Existing venv is broken, recreating..."
		rm -rf "$VENV_DIR"
	fi

	log_info "Creating virtual environment: $VENV_DIR"
	"$python_cmd" -m venv "$VENV_DIR" || {
		log_error "Failed to create venv"
		return 1
	}

	PYTHON_CMD="$VENV_DIR/bin/python"
	log_info "✓ Virtual environment created"
}

install_dependencies() {
	log_step "Checking Python dependencies"

	local pip_cmd="$PYTHON_CMD -m pip"
	local needs_install=false

	for pkg in "${REQUIRED_PACKAGES[@]}"; do
		$PYTHON_CMD -c "import pkg_resources; pkg_resources.require('$pkg')" &>/dev/null || {
			needs_install=true
			break
		}
	done

	$PYTHON_CMD -c "from google.auth.transport.requests import AuthorizedSession" &>/dev/null || needs_install=true

	if [[ "$needs_install" == "false" ]]; then
		log_info "✓ All dependencies installed"
		return 0
	fi

	log_info "Installing dependencies..."
	$pip_cmd install --upgrade pip --quiet 2>/dev/null || true

	if [[ -f "$REQUIREMENTS_FILE" ]]; then
		$pip_cmd install -r "$REQUIREMENTS_FILE" --quiet && {
			log_info "✓ Dependencies installed"
			return 0
		}
	fi

	for pkg in "${REQUIRED_PACKAGES[@]}"; do
		$pip_cmd install "$pkg" --quiet 2>/dev/null || {
			log_error "Failed to install: $pkg"
			return 1
		}
	done

	log_info "✓ Dependencies installed"
}

generate_requirements() {
	[[ -f "$REQUIREMENTS_FILE" ]] && return 0
	cat >"$REQUIREMENTS_FILE" <<'EOF'
google-auth>=2.0.0
google-auth-httplib2>=0.1.0
requests>=2.25.0
EOF
	log_info "✓ Generated requirements.txt"
}

validate_gcp_auth() {
	log_step "Validating Google Cloud authentication"

	check_command gcloud || {
		log_error "Google Cloud SDK required: https://cloud.google.com/sdk/docs/install"
		return 1
	}

	local active_account
	active_account=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || echo "")

	if [[ -z "$active_account" ]]; then
		log_error "No active Google Cloud account. Run: gcloud auth login"
		return 1
	fi
	log_info "✓ Authenticated as: $active_account"

	if [[ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" && ! -f "$HOME/.config/gcloud/application_default_credentials.json" ]]; then
		log_warn "Setting up Application Default Credentials..."
		gcloud auth application-default login --quiet 2>/dev/null || {
			log_error "Failed to setup ADC"
			return 1
		}
	fi
	log_info "✓ Application Default Credentials available"
}

validate_project() {
	log_step "Validating project access"

	gcloud projects describe "$PROJECT_ID" &>/dev/null || {
		log_error "Cannot access project: $PROJECT_ID"
		return 1
	}
	log_info "✓ Project access verified: $PROJECT_ID"

	if ! gcloud services list --project="$PROJECT_ID" --filter="name:notebooks.googleapis.com" --format="value(name)" 2>/dev/null | grep -q "notebooks"; then
		log_info "Enabling Notebooks API..."
		gcloud services enable notebooks.googleapis.com --project="$PROJECT_ID" 2>/dev/null || log_warn "Could not enable Notebooks API"
	fi
	log_info "✓ Notebooks API enabled"
}

check_permissions() {
	log_step "Checking IAM permissions"

	local active_account roles
	active_account=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null)
	roles=$(gcloud projects get-iam-policy "$PROJECT_ID" --flatten="bindings[].members" \
		--format='value(bindings.role)' --filter="bindings.members:$active_account" 2>/dev/null || echo "")

	if echo "$roles" | grep -qE "roles/notebooks.admin|roles/notebooks.runner|roles/editor|roles/owner"; then
		log_info "✓ Sufficient IAM permissions"
	else
		log_warn "Could not verify IAM permissions - operation may fail"
		log_warn "Required roles: notebooks.admin, notebooks.runner, editor, or owner"
	fi
}

display_rollback_warning() {
	echo ""
	echo -e "${YELLOW}${BOLD}⚠️  ROLLBACK SAFETY CHECKS${NC}"
	echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
	echo ""
	echo "Before rolling back, ensure:"
	echo "  1. ✓ You understand what rollback does (reverts to previous version)"
	echo "  2. ✓ Instance was upgraded recently (within rollback window)"
	echo "  3. ✓ You have backed up any important work"
	echo "  4. ✓ You have noted current instance state"
	echo ""
	echo "Rollback will:"
	echo "  • Revert instance to the previous software version"
	echo "  • Restart the instance (temporary downtime)"
	echo "  • Preserve your data and notebooks"
	echo "  • Take several minutes to complete"
	echo ""
	echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
	echo ""
}

cleanup() {
	[[ -n "${VIRTUAL_ENV:-}" ]] && deactivate 2>/dev/null || true
}
trap cleanup EXIT

# Parse arguments
while [[ $# -gt 0 ]]; do
	case $1 in
	--project)
		PROJECT_ID="$2"
		shift 2
		;;
	--locations)
		LOCATIONS="$2"
		shift 2
		;;
	--instance)
		INSTANCE_ID="$2"
		shift 2
		;;
	--dry-run)
		DRY_RUN="true"
		shift
		;;
	--max-parallel)
		MAX_PARALLEL="$2"
		shift 2
		;;
	--timeout)
		TIMEOUT="$2"
		shift 2
		;;
	--poll-interval)
		POLL_INTERVAL="$2"
		shift 2
		;;
	--health-check-timeout)
		HEALTH_CHECK_TIMEOUT="$2"
		shift 2
		;;
	--stagger-delay)
		STAGGER_DELAY="$2"
		shift 2
		;;
	--verbose | -v)
		VERBOSE="true"
		shift
		;;
	--no-venv)
		USE_VENV="false"
		shift
		;;
	--venv-dir)
		VENV_DIR="$2"
		shift 2
		;;
	--python)
		PYTHON_CMD="$2"
		shift 2
		;;
	--skip-venv-check)
		SKIP_VENV_CHECK="true"
		shift
		;;
	--help | -h)
		show_usage
		exit 0
		;;
	*)
		log_error "Unknown option: $1"
		show_usage
		exit 1
		;;
	esac
done

# Validate required args
[[ -z "$PROJECT_ID" ]] && {
	log_error "Project ID required (--project or GCP_PROJECT_ID)"
	show_usage
	exit 1
}
[[ -z "$LOCATIONS" ]] && {
	log_error "Locations required (--locations or LOCATIONS)"
	show_usage
	exit 1
}
[[ ! -f "$PYTHON_SCRIPT_ROLLBACK" ]] && {
	log_error "Python script not found: $PYTHON_SCRIPT_ROLLBACK"
	exit 1
}

print_banner

# Display warning for non-dry-run mode
if [[ "$DRY_RUN" != "true" ]]; then
	display_rollback_warning

	echo -ne "${YELLOW}${BOLD}Do you want to proceed with rollback? (yes/no): ${NC}"
	read -r response
	echo ""

	if [[ ! "$response" =~ ^[Yy][Ee][Ss]$ ]]; then
		log_info "Rollback cancelled by user"
		exit 0
	fi
fi

# Setup
log_step "Running pre-flight checks"
echo ""

SYSTEM_PYTHON=$(find_python) || exit 1
log_info "✓ Found Python: $SYSTEM_PYTHON ($($SYSTEM_PYTHON --version 2>&1))"

generate_requirements
setup_venv "$SYSTEM_PYTHON" || exit 1
install_dependencies || exit 1
validate_gcp_auth || exit 1
validate_project || exit 1
check_permissions

# Start rollback
echo ""
log_step "Starting Rollback Process"
echo ""

# Prefer Python driver if available to ensure robust discovery and API usage
if [[ -f "$PYTHON_SCRIPT_ROLLBACK" ]]; then
	SYSTEM_PY=$(find_python) || exit 1
	setup_venv "$SYSTEM_PY" || exit 1
	ARGS=(
		"--rollback"
		"--project" "$PROJECT_ID"
    "--locations" $LOCATIONS
		"--max-parallel" "$MAX_PARALLEL"
		"--timeout" "$TIMEOUT"
		"--poll-interval" "$POLL_INTERVAL"
		"--health-check-timeout" "$HEALTH_CHECK_TIMEOUT"
		"--stagger-delay" "$STAGGER_DELAY"
	)
	[[ -n "$INSTANCE_ID" ]] && ARGS+=("--instance" "$INSTANCE_ID")
	[[ "$DRY_RUN" == "true" ]] && ARGS+=("--dry-run")
	[[ "$VERBOSE" == "true" ]] && ARGS+=("--verbose")
	log_info "Executing: $PYTHON_CMD $PYTHON_SCRIPT_ROLLBACK ${ARGS[*]}"
	if "$PYTHON_CMD" "$PYTHON_SCRIPT_ROLLBACK" "${ARGS[@]}"; then
		echo ""
		echo -e "${GREEN}${BOLD}========================================================"
		echo " Rollback process completed successfully"
		echo "========================================================${NC}"
		exit 0
	else
		EXIT_CODE=$?
		echo ""
		echo -e "${RED}${BOLD}========================================================"
		echo " Rollback process failed (exit code: $EXIT_CODE)"
		echo "========================================================${NC}"
		log_error "Check workbench-rollback.log and rollback-report-*.json"
		exit $EXIT_CODE
	fi
fi

# Fallback to shell discovery path (kept for parity)
