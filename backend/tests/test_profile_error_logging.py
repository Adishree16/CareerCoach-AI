"""Privacy-safe profile persistence error diagnostics."""

import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import profiles as profile_routes
from app.schemas.auth import AuthenticatedUser
from app.services import profiles as profile_service


class FailingSession:
    def __init__(self, error):
        self.error = error

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def scalar(self, *_args, **_kwargs):
        raise self.error


class ProfileErrorLoggingTests(unittest.TestCase):
    def setUp(self):
        self.sensitive_marker = (
            "PRIVATE_PROFILE_MARKER "
            "postgresql://database-user:database-password-marker@db.example.test/careercoach"
        )

    def _assert_redacted_log(self, log_output):
        self.assertNotIn(self.sensitive_marker, log_output)
        self.assertNotIn("database-password-marker", log_output)
        self.assertNotIn("postgresql://", log_output)
        self.assertIn("exception_type=", log_output)
        self.assertIn("details=redacted", log_output)

    def test_get_profile_logs_redacted_traceback_and_preserves_cause(self):
        db_error = SQLAlchemyError(self.sensitive_marker)
        with self.assertLogs("app.services.profiles", level="ERROR") as captured:
            with self.assertRaises(profile_service.ProfilePersistenceError) as raised:
                profile_service.get_profile(FailingSession(db_error), uuid4())

        self.assertIs(raised.exception.__cause__, db_error)
        self._assert_redacted_log("\n".join(captured.output))
        self.assertIn("profile.get.persistence_failed", "\n".join(captured.output))

    def test_save_profile_logs_redacted_traceback_and_preserves_cause(self):
        db_error = SQLAlchemyError(self.sensitive_marker)
        with self.assertLogs("app.services.profiles", level="ERROR") as captured:
            with self.assertRaises(profile_service.ProfilePersistenceError) as raised:
                profile_service.save_profile(FailingSession(db_error), uuid4(), None)

        self.assertIs(raised.exception.__cause__, db_error)
        self._assert_redacted_log("\n".join(captured.output))
        self.assertIn("profile.save.persistence_failed", "\n".join(captured.output))

    def test_get_route_logs_unexpected_error_without_exposing_exception_text(self):
        error = RuntimeError(self.sensitive_marker)
        user = AuthenticatedUser(id=str(uuid4()), email="safe@example.com")
        with patch.object(profile_routes, "get_profile", side_effect=error):
            with self.assertLogs("app.api.routes.profiles", level="ERROR") as captured:
                with self.assertRaises(HTTPException) as raised:
                    profile_routes.read_my_profile(user, object())

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Profile data could not be retrieved.")
        self.assertIs(raised.exception.__cause__, error)
        self._assert_redacted_log("\n".join(captured.output))

    def test_put_route_logs_unexpected_error_without_exposing_exception_text(self):
        error = RuntimeError(self.sensitive_marker)
        user = AuthenticatedUser(id=str(uuid4()), email="safe@example.com")
        with patch.object(profile_routes, "save_profile", side_effect=error):
            with self.assertLogs("app.api.routes.profiles", level="ERROR") as captured:
                with self.assertRaises(HTTPException) as raised:
                    profile_routes.save_my_profile(None, user, object())

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Profile data could not be saved.")
        self.assertIs(raised.exception.__cause__, error)
        self._assert_redacted_log("\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
