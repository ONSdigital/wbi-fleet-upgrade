"""
Unit tests for WorkbenchRestClient.
"""

import unittest
from unittest.mock import MagicMock, patch
from clients import WorkbenchRestClient
from models import InstanceRef


class TestWorkbenchRestClient(unittest.TestCase):
    """Test WorkbenchRestClient REST API interactions."""

    def setUp(self):
        """Set up test fixtures."""
        with patch("google.auth.default") as mock_auth:
            mock_creds = MagicMock()
            mock_auth.return_value = (mock_creds, None)
            self.client = WorkbenchRestClient(project_id="test-project")

    def test_client_initialization(self):
        """Test client is properly initialised."""
        self.assertEqual(self.client.project_id, "test-project")
        self.assertEqual(self.client.timeout_s, 60)
        self.assertEqual(self.client.max_retries, 5)
        self.assertEqual(self.client.base_delay, 5.0)

    def test_url_construction(self):
        """Test API URL construction."""
        url = self.client._url("projects/test/locations/europe-west2-a/instances")
        self.assertIn("notebooks.googleapis.com/v2", url)
        self.assertIn("projects/test/locations/europe-west2-a/instances", url)

    @patch("clients.AuthorizedSession")
    def test_list_instances_success(self, mock_session_class):
        """Test listing instances successfully."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "instances": [
                {"name": "projects/test/locations/europe-west2-a/instances/inst1"},
                {"name": "projects/test/locations/europe-west2-a/instances/inst2"},
            ]
        }

        self.client.session = mock_session
        mock_session.get.return_value = mock_response

        instances = self.client.list_instances("europe-west2-a")

        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0].short_name, "inst1")
        self.assertEqual(instances[1].short_name, "inst2")
        self.assertEqual(instances[0].location, "europe-west2-a")

    @patch("clients.AuthorizedSession")
    def test_list_instances_with_pagination(self, mock_session_class):
        """Test listing instances with pagination."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # First page
        first_response = MagicMock()
        first_response.status_code = 200
        first_response.json.return_value = {
            "instances": [
                {"name": "projects/test/locations/europe-west2-a/instances/inst1"}
            ],
            "nextPageToken": "token123",
        }

        # Second page
        second_response = MagicMock()
        second_response.status_code = 200
        second_response.json.return_value = {
            "instances": [
                {"name": "projects/test/locations/europe-west2-a/instances/inst2"}
            ]
        }

        self.client.session = mock_session
        mock_session.get.side_effect = [first_response, second_response]

        instances = self.client.list_instances("europe-west2-a")

        self.assertEqual(len(instances), 2)
        self.assertEqual(mock_session.get.call_count, 2)

    @patch("clients.AuthorizedSession")
    def test_get_instance_success(self, mock_session_class):
        """Test getting instance details successfully."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "projects/test/locations/europe-west2-a/instances/inst1",
            "state": "ACTIVE",
        }

        mock_session.get.return_value = mock_response

        instance_data = self.client.get_instance(
            "projects/test/locations/europe-west2-a/instances/inst1"
        )

        self.assertEqual(instance_data["state"], "ACTIVE")

    @patch("clients.AuthorizedSession")
    def test_check_upgradability_upgradeable(self, mock_session_class):
        """Test check upgradability when instance can be upgraded."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "upgradeable": True,
            "upgradeVersion": "M139",
        }

        mock_session.get.return_value = mock_response

        upgradeable, info = self.client.check_upgradability(
            "projects/test/locations/europe-west2-a/instances/inst1"
        )

        self.assertTrue(upgradeable)
        self.assertEqual(info, "M139")

    @patch("clients.AuthorizedSession")
    def test_check_upgradability_not_upgradeable(self, mock_session_class):
        """Test check upgradability when instance is up to date."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "upgradeable": False,
            "upgradeInfo": "Already at latest version",
        }

        mock_session.get.return_value = mock_response

        upgradeable, info = self.client.check_upgradability(
            "projects/test/locations/europe-west2-a/instances/inst1"
        )

        self.assertFalse(upgradeable)
        self.assertIn("latest version", info)

    @patch("clients.AuthorizedSession")
    def test_upgrade_success(self, mock_session_class):
        """Test initiating an upgrade successfully."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "operations/op123"}

        mock_session.post.return_value = mock_response

        op_name = self.client.upgrade(
            "projects/test/locations/europe-west2-a/instances/inst1"
        )

        self.assertEqual(op_name, "operations/op123")

    @patch("clients.AuthorizedSession")
    def test_rollback_success(self, mock_session_class):
        """Test initiating a rollback successfully."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "operations/op456"}

        mock_session.post.return_value = mock_response

        op_name = self.client.rollback(
            "projects/test/locations/europe-west2-a/instances/inst1",
            target_snapshot="snap-123",
        )

        self.assertEqual(op_name, "operations/op456")

    @patch("clients.AuthorizedSession")
    def test_start_instance_success(self, mock_session_class):
        """Test starting an instance successfully."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "operations/op789"}

        mock_session.post.return_value = mock_response

        op_name = self.client.start_instance(
            "projects/test/locations/europe-west2-a/instances/inst1"
        )

        self.assertEqual(op_name, "operations/op789")

    @patch("clients.AuthorizedSession")
    def test_get_operation_success(self, mock_session_class):
        """Test getting operation status successfully."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"done": True, "response": {}}

        mock_session.get.return_value = mock_response

        operation = self.client.get_operation("operations/op123")

        self.assertTrue(operation["done"])

    @patch("clients.AuthorizedSession")
    def test_get_instance_by_name_found(self, mock_session_class):
        """Test getting instance by name when found."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "projects/test/locations/europe-west2-a/instances/inst1",
            "state": "ACTIVE",
        }

        mock_session.get.return_value = mock_response

        instance = self.client.get_instance_by_name("inst1", "europe-west2-a")

        self.assertIsNotNone(instance)
        self.assertEqual(instance.short_name, "inst1")
        self.assertEqual(instance.location, "europe-west2-a")

    @patch("clients.AuthorizedSession")
    def test_get_instance_by_name_not_found(self, mock_session_class):
        """Test getting instance by name when not found."""
        mock_session = MagicMock()
        self.client.session = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"

        mock_session.get.return_value = mock_response

        instance = self.client.get_instance_by_name("inst1", "europe-west2-a")

        self.assertIsNone(instance)

    @patch("clients.AuthorizedSession")
    @patch("clients.time.sleep")
    def test_request_with_retry_on_429(self, mock_sleep, mock_session_class):
        """Test retry logic on 429 rate limit error."""
        mock_session = MagicMock()
        self.client.session = mock_session

        # First request returns 429, second succeeds
        failed_response = MagicMock()
        failed_response.status_code = 429
        failed_response.json.return_value = {"error": {"message": "Rate limit"}}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"done": True}

        mock_session.get.side_effect = [failed_response, success_response]

        result = self.client._request_with_retry("GET", "https://example.com/test")

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(mock_session.get.call_count, 2)
        self.assertTrue(mock_sleep.called)

    @patch("clients.AuthorizedSession")
    def test_calculate_delay_with_retry_after_header(self, mock_session_class):
        """Test delay calculation when Retry-After header is present."""
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "10"}

        delay = self.client._calculate_delay(0, mock_response)

        self.assertEqual(delay, 10.0)

    @patch("clients.AuthorizedSession")
    def test_calculate_delay_for_409_conflict(self, mock_session_class):
        """Test delay calculation for 409 conflict errors."""
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.headers = {}

        delay = self.client._calculate_delay(0, mock_response)

        # Base delay for 409 should be 15 seconds
        self.assertGreater(delay, 10.0)


if __name__ == "__main__":
    unittest.main()
