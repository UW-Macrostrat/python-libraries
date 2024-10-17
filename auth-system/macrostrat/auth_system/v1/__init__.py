"""
Legacy authentication system from Sparrow
"""

from starlette.authentication import AuthenticationError  # noqa
from starlette.middleware.authentication import AuthenticationMiddleware

from .api import AuthAPI, get_backend  # noqa
from .backend import JWTBackend


async def authenticate_request(request):
    """Helper function to authenticate a request against Sparrow's authentication backend."""
    auth_backend = get_backend()
    return await auth_backend.authenticate(request)


async def get_scopes(request):
    """Helper function to get authentication scopes for a request."""
    cred, _ = await authenticate_request(request)
    return cred.scopes
