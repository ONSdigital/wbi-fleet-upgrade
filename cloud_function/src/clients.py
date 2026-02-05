"""
REST API client for Vertex AI Workbench Instances (v2 API).
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import google.auth
from google.auth.transport.requests import AuthorizedSession

from models import InstanceRef

logger = logging.getLogger(__name__)

# v2 API for Workbench Instances
API_BASE = "https://notebooks.googleapis.com/v2"


class WorkbenchRestClient:
    """REST client for Vertex AI Workbench Instances v2 API."""

    RETRYABLE_STATUS_CODES = {409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        project_id: str,
        timeout_s: int = 60,
        max_retries: int = 5,
        base_delay: float = 5.0,
    ):
        """
        Initialize the Workbench REST client.

        Args:
            project_id: GCP project ID
            timeout_s: Request timeout in seconds
            max_retries: Maximum number of retries for transient errors
            base_delay: Base delay for exponential backoff
        """
        self.project_id = project_id
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.base_delay = base_delay

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self.session = AuthorizedSession(creds)

    def _url(self, path: str) -> str:
        """Construct full API URL from path."""
        return f"{API_BASE}/{path.lstrip('/')}"

    def _request_with_retry(self, method: str, url: str, **kwargs) -> dict:
        """
        Execute HTTP request with exponential backoff retry for transient errors.

        Args:
            method: HTTP method (GET, POST)
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            Dictionary with 'response' and 'status_code' keys

        Raises:
            RuntimeError: If max retries exceeded
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                if method.upper() == "GET":
                    resp = self.session.get(url, timeout=self.timeout_s, **kwargs)
                elif method.upper() == "POST":
                    resp = self.session.post(url, timeout=self.timeout_s, **kwargs)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if resp.status_code in self.RETRYABLE_STATUS_CODES:
                    delay = self._calculate_delay(attempt, resp)
                    error_info = ""
                    try:
                        error_data = resp.json()
                        error_info = error_data.get("error", {}).get("message", "")
                    except ValueError as parse_error:
                        logger.debug(
                            "Failed to parse error response as JSON: %s", parse_error
                        )
                    logger.warning(
                        f"Retryable error {resp.status_code} ({error_info}), "
                        f"attempt {attempt + 1}/{self.max_retries + 1}, waiting {delay:.1f}s..."
                    )
                    last_error = (
                        f"HTTP {resp.status_code}: {error_info or resp.text[:200]}"
                    )
                    time.sleep(delay)
                    continue

                return {"response": resp, "status_code": resp.status_code}

            except Exception as e:
                delay = self._calculate_delay(attempt)
                logger.warning(
                    f"Request error: {e}, attempt {attempt + 1}/{self.max_retries + 1}, "
                    f"waiting {delay:.1f}s..."
                )
                last_error = str(e)
                time.sleep(delay)

        raise RuntimeError(f"Max retries exceeded. Last error: {last_error}")

    def _calculate_delay(self, attempt: int, resp=None) -> float:
        """
        Calculate delay with exponential backoff and jitter.

        Args:
            attempt: Current attempt number
            resp: Optional response object (to check Retry-After header)

        Returns:
            Delay in seconds
        """
        if resp and "Retry-After" in resp.headers:
            try:
                return float(resp.headers["Retry-After"])
            except ValueError:
                pass

        # Use longer base delay for 409 (operation queue full)
        base = self.base_delay
        if resp and resp.status_code == 409:
            base = 15.0  # Longer delay for queue conflicts

        delay = base * (2**attempt)
        jitter = delay * 0.2 * (0.5 - time.time() % 1)
        return min(delay + jitter, 180.0)

    def list_instances(self, location: str) -> List[InstanceRef]:
        """
        List all Workbench instances in a location.

        Args:
            location: Zone location (e.g., 'europe-west2-a')

        Returns:
            List of InstanceRef objects

        Raises:
            RuntimeError: If API call fails
        """
        parent = f"projects/{self.project_id}/locations/{location}"
        url = self._url(f"{parent}/instances")

        instances: List[InstanceRef] = []
        page_token: Optional[str] = None

        while True:
            params = {}
            if page_token:
                params["pageToken"] = page_token

            result = self._request_with_retry("GET", url, params=params)
            resp = result["response"]
            if resp.status_code != 200:
                raise RuntimeError(
                    f"List instances failed ({resp.status_code}): {resp.text}"
                )

            data = resp.json()
            for item in data.get("instances", []):
                full_name = item["name"]
                short = full_name.split("/")[-1]
                instances.append(
                    InstanceRef(name=full_name, short_name=short, location=location)
                )

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return instances

    def get_instance(self, instance_name: str) -> Dict:
        """
        Get details of a specific instance.

        Args:
            instance_name: Full instance resource name

        Returns:
            Instance details as dictionary

        Raises:
            RuntimeError: If API call fails
        """
        url = self._url(instance_name)
        result = self._request_with_retry("GET", url)
        resp = result["response"]
        if resp.status_code != 200:
            raise RuntimeError(f"Get instance failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def check_upgradability(self, instance_name: str) -> Tuple[bool, str]:
        """
        Check whether a notebook instance is upgradable.

        Args:
            instance_name: Full instance resource name

        Returns:
            Tuple of (is_upgradeable, upgrade_info)

        Raises:
            RuntimeError: If API call fails
        """
        url = self._url(f"{instance_name}:checkUpgradability")
        result = self._request_with_retry("GET", url)
        resp = result["response"]
        if resp.status_code != 200:
            raise RuntimeError(
                f"checkUpgradability failed ({resp.status_code}): {resp.text}"
            )

        data = resp.json()
        upgradeable = bool(data.get("upgradeable", False))
        upgrade_version = data.get("upgradeVersion", "")
        upgrade_info = data.get("upgradeInfo", "")

        info = (
            upgrade_version
            if upgrade_version
            else upgrade_info if upgrade_info else "N/A"
        )
        return upgradeable, info

    def upgrade(self, instance_name: str) -> str:
        """
        Initiate upgrade for an instance.

        Args:
            instance_name: Full instance resource name

        Returns:
            Operation name

        Raises:
            RuntimeError: If API call fails
        """
        url = self._url(f"{instance_name}:upgrade")
        result = self._request_with_retry("POST", url, json={})
        resp = result["response"]
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"upgrade failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        if "name" not in data:
            raise RuntimeError(f"upgrade returned unexpected response: {data}")
        return data["name"]

    def rollback(
        self, instance_name: str, target_snapshot: Optional[str] = None
    ) -> str:
        """
        Initiate rollback for an instance.

        Args:
            instance_name: Full instance resource name
            target_snapshot: Optional snapshot resource to rollback to

        Returns:
            Operation name

        Raises:
            RuntimeError: If API call fails
        """
        url = self._url(f"{instance_name}:rollback")
        body = {"targetSnapshot": target_snapshot} if target_snapshot else {}

        result = self._request_with_retry("POST", url, json=body)
        resp = result["response"]
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"rollback failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        if "name" not in data:
            raise RuntimeError(f"rollback returned unexpected response: {data}")
        return data["name"]

    def start_instance(self, instance_name: str) -> str:
        """
        Initiate start for a stopped/suspended instance.

        Args:
            instance_name: Full instance resource name

        Returns:
            Operation name

        Raises:
            RuntimeError: If API call fails
        """
        url = self._url(f"{instance_name}:start")
        result = self._request_with_retry("POST", url, json={})
        resp = result["response"]
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"start failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        if "name" not in data:
            raise RuntimeError(f"start returned unexpected response: {data}")
        return data["name"]

    def get_operation(self, op_name: str) -> Dict:
        """
        Get status of a long-running operation.

        Args:
            op_name: Operation name

        Returns:
            Operation details as dictionary

        Raises:
            RuntimeError: If API call fails
        """
        url = self._url(op_name)
        result = self._request_with_retry("GET", url)
        resp = result["response"]
        if resp.status_code != 200:
            raise RuntimeError(
                f"Get operation failed ({resp.status_code}): {resp.text}"
            )
        return resp.json()

    def get_instance_by_name(
        self, instance_id: str, location: str
    ) -> Optional[InstanceRef]:
        """
        Get an instance reference by instance ID and location.

        Args:
            instance_id: Instance short name (ID)
            location: Zone location (e.g., 'europe-west2-a')

        Returns:
            InstanceRef if found, None otherwise

        Raises:
            RuntimeError: If API call fails
        """
        full_name = (
            f"projects/{self.project_id}/locations/{location}/instances/{instance_id}"
        )
        try:
            self.get_instance(full_name)
            return InstanceRef(
                name=full_name, short_name=instance_id, location=location
            )
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise
