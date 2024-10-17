from contextvars import ContextVar

from .backend import JWTBackend
from .identity import IdentityProvider

_auth_backend: ContextVar[JWTBackend] = ContextVar("auth_backend")
_identity_provider: ContextVar[IdentityProvider] = ContextVar("identity_provider")


def get_backend() -> JWTBackend:
    backend = _auth_backend.get()
    if backend is None:
        raise RuntimeError("No authentication backend configured")
    return backend


def get_identity_provider() -> IdentityProvider:
    provider = _identity_provider.get()
    if provider is None:
        raise RuntimeError("No identity provider configured")
    return provider


def set_identity_provider(provider: IdentityProvider):
    _identity_provider.set(provider)


def create_backend(secret_key: str):
    backend = JWTBackend(secret_key)
    _auth_backend.set(backend)
    return backend


def get_secret_key():
    backend = get_backend()
    return backend.encode_key
