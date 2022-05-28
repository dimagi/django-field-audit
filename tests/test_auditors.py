from subprocess import CalledProcessError
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings

from field_audit.auditors import (
    BaseAuditor,
    RequestAuditor,
    SystemUserAuditor,
    audit_dispatcher,
)
from field_audit.models import (
    USER_TYPE_PROCESS,
    USER_TYPE_REQUEST,
    USER_TYPE_TTY,
)


class TestAuditDispatcher(TestCase):

    def setUp(self):
        # reset the auditor chain to default values
        audit_dispatcher.setup_auditors()

    def test_audit_dispatcher_default_auditor_chain(self):
        with self.assertRaises(AttributeError):
            getattr(settings, "FIELD_AUDIT_AUDITORS")
        request_auditor, sysuser_auditor = audit_dispatcher.auditors
        self.assertIsInstance(request_auditor, RequestAuditor)
        self.assertIsInstance(sysuser_auditor, SystemUserAuditor)

    @override_settings(
        FIELD_AUDIT_AUDITORS=[
            "tests.test_auditors.CustomAuditor1",
            "tests.test_auditors.CustomAuditor2",
            "tests.test_auditors.CustomAuditor3",
        ],
    )
    def test_audit_dispatcher_auditor_chain_is_configurable(self):
        audit_dispatcher.setup_auditors()
        one, two, three = audit_dispatcher.auditors
        self.assertIsInstance(one, CustomAuditor1)
        self.assertIsInstance(two, CustomAuditor2)
        self.assertIsInstance(three, CustomAuditor3)

    @override_settings(FIELD_AUDIT_AUDITORS=[])
    def test_audit_dispatcher_setting_empty_auditor_chain_clears_auditors(self):
        audit_dispatcher.setup_auditors()
        self.assertEqual([], audit_dispatcher.auditors)

    @override_settings(FIELD_AUDIT_AUDITORS=["tests.models.Flight"])
    def test_audit_dispatcher_custom_auditors_must_subclass_baseauditor(self):
        from .models import Flight
        self.assertFalse(issubclass(Flight, BaseAuditor))
        with self.assertRaises(ValueError):
            audit_dispatcher.setup_auditors()

    def test_audit_dispatcher_chain(self):
        aud1 = MockAuditor(True)
        aud2 = MockAuditor(True)
        chain = [aud1, aud2]

        # aud1 hits, aud2 never called
        with patch.object(audit_dispatcher, "auditors", chain):
            changed_by = audit_dispatcher.dispatch(object())
        self.assertIs(changed_by, aud1)
        self.assertEqual(aud1.dispatched, 1)
        self.assertEqual(aud2.dispatched, 0)

        # aud1 misses, aud2 hits
        aud1.reset(False)
        with patch.object(audit_dispatcher, "auditors", chain):
            changed_by = audit_dispatcher.dispatch(object())
        self.assertIs(changed_by, aud2)
        self.assertEqual(aud1.dispatched, 1)
        self.assertEqual(aud2.dispatched, 1)

        # both miss
        aud1.reset(False)
        aud2.reset(False)
        with patch.object(audit_dispatcher, "auditors", chain):
            changed_by = audit_dispatcher.dispatch(object())
        self.assertIsNone(changed_by)
        self.assertEqual(aud1.dispatched, 1)
        self.assertEqual(aud2.dispatched, 1)


class CustomAuditor1(BaseAuditor):
    pass


class CustomAuditor2(BaseAuditor):
    pass


class CustomAuditor3(BaseAuditor):
    pass


class MockAuditor:

    def __init__(self, enabled):
        self.reset(enabled)

    def reset(self, enabled):
        self.dispatched = 0
        self.enabled = enabled

    def changed_by(self, request):
        self.dispatched += 1
        return self if self.enabled else None


class TestBaseAuditor(TestCase):

    def test_baseauditor_changed_by_raises_notimplementederror(self):
        with self.assertRaises(NotImplementedError):
            BaseAuditor().changed_by(object())


class TestSystemUserAuditor(TestCase):

    def setUp(self):
        super().setUp()
        self.auditor = SystemUserAuditor()

    def test_systemuserauditor_changed_by_returns_sys_value_for_request(self):
        def user(*args, **kw):
            return b"test ..."
        with patch("field_audit.auditors.check_output", side_effect=user):
            self.assertEqual(
                {"user_type": USER_TYPE_TTY, "username": "test"},
                self.auditor.changed_by(object()),
            )

    def _patch_system_getters_and_validate(self, fake_output, changed_by):
        kwargs = {"side_effect": fake_output}
        user = None if changed_by is None else changed_by["username"]
        with (
            patch("field_audit.auditors.check_output", **kwargs) as chk_out,
            patch("field_audit.auditors.getuser", return_value=user) as getuser,
        ):
            audit_info = self.auditor.changed_by(None)
            if isinstance(audit_info, dict):
                self.assertEqual(changed_by, audit_info)
            else:
                self.assertIsNone(audit_info)
            return chk_out, getuser

    def test_systemuserauditor_changed_by_tolerates_invalid_who_output(self):
        def bogus(*args, **kw):
            return b"\xb4"
        ch_by = {"user_type": USER_TYPE_PROCESS, "username": "alice"}
        chk_out, getuser = self._patch_system_getters_and_validate(bogus, ch_by)
        chk_out.assert_called_once()
        getuser.assert_called_once()

    def test_systemuserauditor_changed_by_tolerates_empty_who_output(self):
        def empty(*args, **kw):
            return b""
        ch_by = {"user_type": USER_TYPE_PROCESS, "username": "bob"}
        chk_out, getuser = self._patch_system_getters_and_validate(empty, ch_by)
        chk_out.assert_called_once()
        getuser.assert_called_once()

    def test_systemuserauditor_remembers_missing_who_bin(self):
        def fail(*args, **kw):
            raise CalledProcessError(1, [], "")
        ch_by = {"user_type": USER_TYPE_PROCESS, "username": "carlos"}
        # round 1
        chk_out, getuser = self._patch_system_getters_and_validate(fail, ch_by)
        chk_out.assert_called_once()
        getuser.assert_called_once()
        # round 2
        chk_out, getuser = self._patch_system_getters_and_validate(fail, ch_by)
        chk_out.assert_not_called()
        getuser.assert_called_once()

    def test_systemuserauditor_changed_by_returns_tty_user_on_who_output(self):
        def mock(*args, **kw):
            return b"eve  ttys000  May 8 17:04  (localhost)"
        ch_by = {"user_type": USER_TYPE_TTY, "username": "eve"}
        chk_out, getuser = self._patch_system_getters_and_validate(mock, ch_by)
        chk_out.assert_called_once()
        getuser.assert_not_called()

    def test_systemuserauditor_changed_by_returns_none_if_all_else_fails(self):
        def empty(*args, **kw):
            return b""
        chk_out, getuser = self._patch_system_getters_and_validate(empty, None)
        chk_out.assert_called_once()
        getuser.assert_called_once()


class TestRequestAuditor(TestCase):

    def setUp(self):
        self.request = AuthedRequest()
        self.auditor = RequestAuditor()

    def test_requestauditor_changed_by(self):
        self.assertEqual(
            {
                "user_type": USER_TYPE_REQUEST,
                "username": self.request.user.username,
            },
            self.auditor.changed_by(self.request),
        )

    def test_requestauditor_changed_by_returns_none_without_request(self):
        self.assertIsNone(self.auditor.changed_by(None))

    def test_requestauditor_changed_by_returns_value_for_unauthorized_req(self):
        self.request.deauth()
        self.assertEqual({}, self.auditor.changed_by(self.request))


class AuthedRequest:

    class User:
        username = "test@example.com"
        is_authenticated = True

    def __init__(self):
        self.user = self.User()

    def deauth(self):
        self.user.is_authenticated = False
