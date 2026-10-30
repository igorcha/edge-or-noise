"""Allow-any-origin CORS for the Vite dev server. Off by default: in production Django serves API and app same-origin."""

from django.conf import settings
from django.http import HttpResponse


def cors(get_response):
    def middleware(request):
        if not settings.CORS_ALLOW_ALL:
            return get_response(request)
        response = HttpResponse(status=204) if request.method == "OPTIONS" else get_response(request)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response
    return middleware
