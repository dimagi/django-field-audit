from .field_audit import set_request


class FieldAuditMiddleware:
    """Middleware that gives the ``audit_fields()`` decorator access to the
    Django request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view_func, view_args, view_kwargs):
        set_request(request)
        return None

    def __call__(self, request):
        return self.get_response(request)
