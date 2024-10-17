from http.client import HTTPException
from uuid import uuid4

from pytest import fixture, mark
from starlette.applications import Starlette
from starlette.authentication import requires
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

from . import get_backend
from .api import AuthAPI
from .context import create_backend, set_identity_provider, get_identity_provider
from .identity import BaseUser, IdentityProvider


async def http_exception(request: Request, exc: HTTPException):
    return JSONResponse(
        {
            "detail": exc.detail,
            "error": exc.error,
        },
        status_code=exc.status_code,
    )


class MockDatabase(IdentityProvider):
    def __init__(self):
        self.users = {}

    def create_user(self, username, password):
        self.users[username] = BaseUser(username, password)

    def get_user(self, username):
        return self.users.get(username)


# Set context variables outside of fixtures, or else they don't propagate apparently
create_backend(uuid4().hex)
provider = MockDatabase()
set_identity_provider(provider)


@fixture(scope="session")
def secret_key():
    bak = get_backend()
    return bak.encode_key


@fixture(scope="class")
def auth_backend():
    return get_backend()


@fixture(scope="class")
def admin_client(app, db):
    user = "Test"
    password = "test"
    client = TestClient(app)
    # Create a user directly on the database
    db.create_user(user, password)

    client.post("/auth/login", json={"username": user, "password": password})
    return client


@fixture(scope="class")
def app(auth_backend):
    # Create a Starlette app
    _app = Starlette(exception_handlers={HTTPException: http_exception})
    _app.add_middleware(AuthenticationMiddleware, backend=auth_backend)

    # Mount the AuthAPI routes
    _app.mount("/auth", AuthAPI, name="auth")
    return _app


@fixture(scope="class")
def db():
    """Mocked database for checking users"""
    return get_identity_provider()


@fixture(scope="class")
def client(app, db):
    _client = TestClient(app)
    yield _client


def is_forbidden(res):
    return res.status_code == 403 and res.reason_phrase == "Forbidden"


def get_access_cookie(response):
    return {"access_token_cookie": response.cookies.get("access_token_cookie")}


def verify_credentials(client, cred):
    login = client.post("/auth/login", json=cred)
    res = client.get("/auth/status", cookies=get_access_cookie(login))
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == cred["username"]
    assert data["login"]
    return res


bad_credentials = [
    {"username": "TestA", "password": "test"},
    {"username": "TestA", "password": "xxxxx"},
    {"username": "", "password": ""},
    {},
    None,
]


def setup_ws_test_routes(app):
    @app.websocket_route("/ws-test")
    async def ws_route(websocket):
        await websocket.accept()
        await websocket.send_json({"enabled": True})

    @app.websocket_route("/ws-auth-test")
    @requires("admin")
    async def ws_auth_route(websocket):
        await websocket.accept()
        data = await websocket.receive_json()
        await websocket.send_json(
            {
                "authenticated": websocket.user.is_authenticated,
                "user": websocket.user.username,
            }
        )
        await websocket.close()


def test_websocket_send_and_receive_json():
    def app(scope):
        async def asgi(receive, send):
            websocket = WebSocket(scope, receive=receive, send=send)
            await websocket.accept()
            data = await websocket.receive_json()
            await websocket.send_json({"message": data})
            await websocket.close()

        return asgi

    client = TestClient(app)
    with client.websocket_connect("/") as websocket:
        websocket.send_json({"hello": "world"})
        data = websocket.receive_json()
        assert data == {"message": {"hello": "world"}}


class TestBasicAuth:
    def test_create_user(self, db):
        user = "Test"
        password = "test"
        db.create_user(user, password)

    def test_jwt_encoding(self, auth_backend):
        value = "Test123"
        token = auth_backend._encode(payload=dict(value=value))
        res = auth_backend._decode(token)
        assert res["value"] == value

    def test_ensure_user_created(self, db):
        user = db.get_user("Test")
        assert user.username == "Test"

    def test_forbidden(self, client):
        res = client.get("/auth/secret")
        assert is_forbidden(res)

    def test_login(self, client, auth_backend):
        res = client.post("/auth/login", json={"username": "Test", "password": "test"})
        data = res.json()
        assert "error" not in data
        assert data["username"] == "Test"
        assert data["login"]
        for type in ("access", "refresh"):
            token = res.cookies.get(f"{type}_token_cookie")
            payload = auth_backend._decode(token)
            assert payload.get("type") == type
            assert payload.get("identity") == "Test"

    @mark.parametrize("bad_credentials", bad_credentials)
    def test_bad_login(self, client, bad_credentials):
        res = client.post("/auth/login", json=bad_credentials)
        assert res.status_code in [401, 422]
        if res.status_code == 401:
            assert not res.json()["login"]

    def test_invalid_token(self, client):
        res = client.get("/auth/secret", cookies={"access_token_cookie": "ekadqw4fw"})
        assert is_forbidden(res)

    def test_status(self, client):
        """We should be logged out"""
        res = client.get("/auth/status")
        assert res.status_code == 200
        data = res.json()
        assert not data["login"]
        assert data["username"] is None

    def test_login_flow(self, client):
        res = verify_credentials(client, {"username": "Test", "password": "test"})
        secret = client.get("/auth/secret", cookies=get_access_cookie(res))
        assert secret.status_code == 200
        data = secret.json()
        assert data["answer"] == 42

    def test_invalid_login(self, client):
        try:
            res = verify_credentials(
                client, {"username": "TestAAA", "password": "test"}
            )
        except AssertionError:
            # We expect an assertion error here...
            return
        assert False

    def test_access_token(self, client):
        res = client.post("/auth/login", json={"username": "Test", "password": "test"})
        data = res.json()
        assert "error" not in data

        token = data["token"]

        res = client.get("/auth/secret")
        data = res.json()
        assert "error" not in data
        assert data["answer"] == 42

    @mark.xfail(reason="Websocket testing doesn't seem to work")
    def test_websocket_access(self, app):
        setup_ws_test_routes(app)
        client = TestClient(app)
        with client.websocket_connect("/ws-auth-test") as websocket:
            websocket.send_json({"hello": "world"})
            data = websocket.receive_json()
            assert data == {"authenticated": True, "user": "Test"}
