from starlette.routing import Route, Router
from starlette.responses import JSONResponse
from starlette.authentication import requires, AuthenticationError
from macrostrat.utils import get_logger

from webargs_starlette import use_annotations
from .context import get_backend, get_identity_provider

log = get_logger(__name__)


def UnauthorizedResponse(**kwargs):
    return JSONResponse(
        dict(login=False, username=None, message="user is not authenticated"), **kwargs
    )


@use_annotations(location="json")
async def login(request, username: str, password: str):
    backend = get_backend()
    users = get_identity_provider()
    current_user = users.get_user(username)

    log.debug(current_user)

    if current_user is not None and current_user.is_correct_password(password):
        day = 24 * 60 * 60
        token = backend.set_cookie(
            None, "access", max_age=day, identity=username, role="admin"
        )
        resp = JSONResponse(dict(login=True, username=username, token=token))
        return backend.set_login_cookies(resp, identity=username)

    return backend.logout(UnauthorizedResponse(status_code=401))


def logout(request):
    backend = get_backend()
    return backend.logout(UnauthorizedResponse(status_code=200))


def status(request):
    backend = get_backend()
    try:
        identity = backend.get_identity(request)
        return JSONResponse(dict(login=True, username=identity))
    except AuthenticationError:
        # We have to handle authentication errors to return a 200 response
        # even though the user is logged out
        return UnauthorizedResponse(status_code=200)


def refresh(request):
    # JWT refresh token required
    backend = get_backend()
    identity = backend.get_identity(request, type="refresh")
    response = JSONResponse(dict(login=True, refresh=True, username=identity))

    return backend.set_access_cookie(response, identity=identity, role="admin")


@requires("admin")
def secret(request):
    # JWT required
    return JSONResponse({"answer": 42})


AuthAPI = Router(
    [
        Route("/login", endpoint=login, methods=["POST"]),
        Route("/logout", endpoint=logout, methods=["POST"]),
        Route("/refresh", endpoint=refresh, methods=["POST"]),
        Route("/status", endpoint=status),
        Route("/secret", endpoint=secret),
    ]
)
