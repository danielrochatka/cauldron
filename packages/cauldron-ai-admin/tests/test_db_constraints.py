"""Database-level CHECK constraints on audit tables.

These tests exercise the constraints via ``TestCase.assertRaises`` on
``IntegrityError`` — the wrapping transactions guarantee the failed row
is rolled back rather than left in the database.
"""
from __future__ import annotations

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone as _tz


class AdminAIToolInvocationStatusEnumTest(TestCase):
    """Invalid status value on AdminAIToolInvocation must be rejected."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from cauldron_ai_admin.models import AdminAIRun
        User = get_user_model()
        cls.user = User.objects.create(username="dbconstr")
        cls.admin_run = AdminAIRun.objects.create(
            actor=cls.user,
            status="created",
            provider_name="fake",
            user_request="hi",
        )

    def test_invalid_invocation_status_raises_integrity_error(self):
        from cauldron_ai_admin.models import AdminAIToolInvocation
        with self.assertRaises(IntegrityError):
            AdminAIToolInvocation.objects.create(
                run=self.admin_run,
                tool_name="t.x",
                risk_level="READ_ONLY",
                status="bogus-status",
            )

    def test_invalid_invocation_risk_level_raises_integrity_error(self):
        from cauldron_ai_admin.models import AdminAIToolInvocation
        with self.assertRaises(IntegrityError):
            AdminAIToolInvocation.objects.create(
                run=self.admin_run,
                tool_name="t.x",
                risk_level="NUCLEAR",
                status="requested",
            )


class AdminAIRunCompletionTimestampTest(TestCase):
    """AdminAIRun completed_at must be null iff not active."""

    def test_running_with_completed_at_is_rejected(self):
        from django.contrib.auth import get_user_model
        from cauldron_ai_admin.models import AdminAIRun
        User = get_user_model()
        user = User.objects.create(username="compat1")
        with self.assertRaises(IntegrityError):
            AdminAIRun.objects.create(
                actor=user,
                status="running",
                provider_name="fake",
                user_request="hi",
                completed_at=_tz.now(),
            )

    def test_terminal_without_completed_at_is_rejected(self):
        from django.contrib.auth import get_user_model
        from cauldron_ai_admin.models import AdminAIRun
        User = get_user_model()
        user = User.objects.create(username="compat2")
        with self.assertRaises(IntegrityError):
            AdminAIRun.objects.create(
                actor=user,
                status="completed",
                provider_name="fake",
                user_request="hi",
                completed_at=None,
            )


class AdminAIToolInvocationCompletionTimestampTest(TestCase):
    """AdminAIToolInvocation completed_at must be null iff not active."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from cauldron_ai_admin.models import AdminAIRun
        User = get_user_model()
        cls.user = User.objects.create(username="ivcompl")
        cls.admin_run = AdminAIRun.objects.create(
            actor=cls.user,
            status="created",
            provider_name="fake",
            user_request="hi",
        )

    def test_running_invocation_with_completed_at_is_rejected(self):
        from cauldron_ai_admin.models import AdminAIToolInvocation
        with self.assertRaises(IntegrityError):
            AdminAIToolInvocation.objects.create(
                run=self.admin_run,
                tool_name="t.x",
                risk_level="READ_ONLY",
                status="running",
                completed_at=_tz.now(),
            )

    def test_terminal_invocation_without_completed_at_is_rejected(self):
        from cauldron_ai_admin.models import AdminAIToolInvocation
        with self.assertRaises(IntegrityError):
            AdminAIToolInvocation.objects.create(
                run=self.admin_run,
                tool_name="t.x",
                risk_level="READ_ONLY",
                status="completed",
                completed_at=None,
            )
