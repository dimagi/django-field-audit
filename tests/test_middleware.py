from django.test import TestCase

from field_audit.field_audit import _get_request, _thread
from field_audit.middleware import FieldAuditMiddleware


class TestFieldAuditMiddleware(TestCase):

    def test_field_audit_middleware(self):
        middleware = FieldAuditMiddleware(lambda x: x)
        request = object()
        self.assertIs(request, middleware(request))
        middleware.process_view(request, None, None, None)
        try:
            self.assertIs(request, _get_request())
        finally:
            # clear the request to keep test env sterile
            del _thread.request
