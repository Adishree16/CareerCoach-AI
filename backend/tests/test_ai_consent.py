"""Focused tests for explicit, user-scoped AI personalization consent."""

import importlib.util
import unittest
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api import router as api_router_module
from app.api.routes import ai_consent
from app.api.routes.auth import get_current_user, require_csrf_protection
from app.database.session import get_db
from app.models.ai_consent import AIPersonalizationConsent
from app.schemas.ai_consent import AIConsentDecisionRequest
from app.schemas.auth import AuthenticatedUser
from app.services.ai_consent import (
    CURRENT_AI_CONSENT_NOTICE_VERSION,
    CURRENT_AI_CONSENT_SCOPE_VERSION,
    AIConsentVersionError,
    NoActiveAIConsentError,
    get_ai_consent,
    has_current_ai_consent,
    save_ai_consent,
    withdraw_ai_consent,
)


NOTICE_VERSION = CURRENT_AI_CONSENT_NOTICE_VERSION
SCOPE_VERSION = CURRENT_AI_CONSENT_SCOPE_VERSION


class BrokenDatabase:
    def get(self, *_args, **_kwargs):
        raise OperationalError("select consent", {}, RuntimeError("database unavailable"))


class AIConsentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        AIPersonalizationConsent.__table__.create(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db: Session = self.session_factory()
        self.user_id = uuid4()
        self.other_user_id = uuid4()
        self.user = AuthenticatedUser(id=str(self.user_id), email="not-used@example.test")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def save(self, decision="granted", *, notice=NOTICE_VERSION, scope=SCOPE_VERSION, user_id=None):
        return save_ai_consent(
            self.db,
            self.user_id if user_id is None else user_id,
            decision,
            notice,
            scope,
        )

    def test_missing_record_returns_not_set_and_current_versions(self):
        result = get_ai_consent(self.db, self.user_id)
        self.assertEqual(result["state"], "not_set")
        self.assertIsNone(result["notice_version"])
        self.assertIsNone(result["scope_version"])
        self.assertIsNone(result["decision_at"])
        self.assertEqual(result["current_notice_version"], NOTICE_VERSION)
        self.assertEqual(result["current_scope_version"], SCOPE_VERSION)
        self.assertFalse(has_current_ai_consent(self.db, self.user_id))

    def test_granting_consent_is_bound_to_authenticated_user(self):
        result = self.save()
        self.assertEqual(result["state"], "granted")
        self.assertEqual(result["notice_version"], NOTICE_VERSION)
        self.assertEqual(result["scope_version"], SCOPE_VERSION)
        self.assertIsNotNone(result["decision_at"])
        self.assertIsNotNone(result["granted_at"])
        self.assertIsNone(result["withdrawn_at"])
        self.assertTrue(has_current_ai_consent(self.db, self.user_id))
        self.assertFalse(has_current_ai_consent(self.db, self.other_user_id))
        self.assertNotIn("user_id", result)
        self.assertNotIn("profile_id", result)

    def test_declining_consent_denies_ai_processing(self):
        result = self.save("declined")
        self.assertEqual(result["state"], "declined")
        self.assertIsNotNone(result["decision_at"])
        self.assertIsNone(result["granted_at"])
        self.assertIsNone(result["withdrawn_at"])
        self.assertFalse(has_current_ai_consent(self.db, self.user_id))

    def test_withdrawal_denies_future_processing_and_records_timestamp(self):
        self.save("granted")
        result = withdraw_ai_consent(self.db, self.user_id)
        self.assertEqual(result["state"], "withdrawn")
        self.assertIsNotNone(result["granted_at"])
        self.assertIsNotNone(result["withdrawn_at"])
        self.assertFalse(has_current_ai_consent(self.db, self.user_id))

    def test_regrant_after_withdrawal_restores_current_consent(self):
        self.save("granted")
        withdraw_ai_consent(self.db, self.user_id)
        result = self.save("granted")
        self.assertEqual(result["state"], "granted")
        self.assertIsNotNone(result["granted_at"])
        self.assertIsNone(result["withdrawn_at"])
        self.assertTrue(has_current_ai_consent(self.db, self.user_id))

    def test_outdated_notice_or_scope_is_rejected(self):
        for notice, scope in (("old-notice", SCOPE_VERSION), (NOTICE_VERSION, "old-scope")):
            with self.subTest(notice=notice, scope=scope), self.assertRaises(AIConsentVersionError):
                self.save(notice=notice, scope=scope)
        self.assertEqual(get_ai_consent(self.db, self.user_id)["state"], "not_set")

    def test_withdrawal_without_active_grant_is_rejected(self):
        with self.assertRaises(NoActiveAIConsentError):
            withdraw_ai_consent(self.db, self.user_id)
        self.save("declined")
        with self.assertRaises(NoActiveAIConsentError):
            withdraw_ai_consent(self.db, self.user_id)

    def test_invalid_decision_and_extra_identity_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            AIConsentDecisionRequest.model_validate({
                "decision": "withdrawn",
                "notice_version": NOTICE_VERSION,
                "scope_version": SCOPE_VERSION,
            })
        with self.assertRaises(ValidationError):
            AIConsentDecisionRequest.model_validate({
                "decision": "granted",
                "notice_version": NOTICE_VERSION,
                "scope_version": SCOPE_VERSION,
                "user_id": str(self.other_user_id),
            })
        with self.assertRaises(ValidationError):
            AIConsentDecisionRequest.model_validate({
                "decision": "granted",
                "notice_version": NOTICE_VERSION,
                "scope_version": SCOPE_VERSION,
                "profile_id": str(self.other_user_id),
            })

    def test_routes_are_registered_authenticated_and_write_routes_require_csrf(self):
        app = FastAPI()
        app.include_router(api_router_module.api_router, prefix="/api")
        paths = app.openapi()["paths"]
        self.assertIn("/api/ai-consent", paths)
        self.assertIn("/api/ai-consent/withdraw", paths)
        self.assertEqual(set(paths["/api/ai-consent"]), {"get", "post"})
        self.assertEqual(set(paths["/api/ai-consent/withdraw"]), {"post"})

        route_by_path = {
            route.path: route
            for route in ai_consent.router.routes
            if isinstance(route, APIRoute)
        }
        for path in ("/ai-consent", "/ai-consent/withdraw"):
            route = route_by_path[path]
            dependencies = {item.call for item in route.dependant.dependencies}
            self.assertIn(get_current_user, dependencies)
            self.assertIn(get_db, dependencies)
            if "POST" in route.methods:
                self.assertIn(require_csrf_protection, dependencies)

    def test_route_reads_and_writes_only_the_authenticated_users_record(self):
        self.save("granted", user_id=self.other_user_id)
        self.assertEqual(ai_consent.read_ai_consent(self.user, self.db)["state"], "not_set")

        request = AIConsentDecisionRequest.model_validate({
            "decision": "granted",
            "noticeVersion": NOTICE_VERSION,
            "scopeVersion": SCOPE_VERSION,
        })
        result = ai_consent.decide_ai_consent(request, self.user, self.db)
        self.assertEqual(result["state"], "granted")
        self.assertEqual(get_ai_consent(self.db, self.other_user_id)["state"], "granted")
        self.assertEqual(get_ai_consent(self.db, self.user_id)["state"], "granted")

    def test_route_rejects_invalid_authenticated_uuid(self):
        invalid_user = AuthenticatedUser(id="not-a-uuid", email="ignored@example.test")
        with self.assertRaises(HTTPException) as raised:
            ai_consent.read_ai_consent(invalid_user, self.db)
        self.assertEqual(raised.exception.status_code, 401)

    def test_outdated_request_and_invalid_withdrawal_return_safe_statuses(self):
        outdated = AIConsentDecisionRequest.model_validate({
            "decision": "granted",
            "notice_version": "old-notice",
            "scope_version": SCOPE_VERSION,
        })
        with self.assertRaises(HTTPException) as raised:
            ai_consent.decide_ai_consent(outdated, self.user, self.db)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail, "The AI consent notice must be reviewed again.")

        with self.assertRaises(HTTPException) as raised:
            ai_consent.withdraw_my_ai_consent(self.user, self.db)
        self.assertEqual(raised.exception.status_code, 409)

    def test_database_failure_fails_closed_and_route_returns_safe_error(self):
        self.assertFalse(has_current_ai_consent(BrokenDatabase(), self.user_id))
        with self.assertRaises(HTTPException) as raised:
            ai_consent.read_ai_consent(self.user, BrokenDatabase())
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "AI consent could not be processed.")

    def test_sqlalchemy_model_is_registered_and_matches_migration(self):
        from app.database.session import Base
        import app.models

        self.assertIn("ai_personalization_consents", Base.metadata.tables)
        self.assertIs(app.models.AIPersonalizationConsent, AIPersonalizationConsent)
        expected_columns = {
            "user_id", "consent_state", "notice_version", "scope_version",
            "decision_at", "granted_at", "withdrawn_at", "created_at", "updated_at",
        }
        self.assertEqual(set(AIPersonalizationConsent.__table__.columns.keys()), expected_columns)

    def test_migration_upgrade_and_downgrade_work_on_isolated_sqlite(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "a81d6f2c9b40_add_ai_personalization_consent.py"
        )
        spec = importlib.util.spec_from_file_location("ai_consent_migration", migration_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.revision, "a81d6f2c9b40")
        self.assertEqual(module.down_revision, "9c1f4a6e2b70")

        migration_engine = create_engine("sqlite+pysqlite:///:memory:")
        with migration_engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("ai_personalization_consents")}
            self.assertEqual(
                columns,
                {
                    "user_id", "consent_state", "notice_version", "scope_version",
                    "decision_at", "granted_at", "withdrawn_at", "created_at", "updated_at",
                },
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.downgrade()
            self.assertNotIn("ai_personalization_consents", inspect(connection).get_table_names())
        migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
