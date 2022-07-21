import contextvars

from django.test import TestCase

from field_audit.field_audit import request as audit_request
from field_audit.middleware import FieldAuditMiddleware


class TestFieldAuditMiddleware(TestCase):

    def test_field_audit_middleware_call(self):
        middleware = FieldAuditMiddleware(lambda x: x)
        request = object()
        self.assertIs(request, middleware(request))

    def test_field_audit_middleware_process_view(self):
        def test():
            middleware = FieldAuditMiddleware(None)
            request = object()
            self.assertIsNone(audit_request.get())
            middleware.process_view(request, None, None, None)
            self.assertIs(request, audit_request.get())
        # run the test in a separate context to keep the test env sterile
        context = contextvars.copy_context()
        context.run(test)
