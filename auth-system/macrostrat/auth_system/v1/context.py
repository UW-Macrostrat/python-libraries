from contextvars import ContextVar
from .backend import JWTBackend

auth_backend: ContextVar[JWTBackend] = ContextVar("auth_backend")

def get_backend():
    backend = auth_backend.get()
    if backend is None:
        raise RuntimeError("No authentication backend configured")
    return backend

def create_backend(secret_key: str):
    backend = JWTBackend(secret_key)
    auth_backend.set(backend)
    return backend

def get_secret_key():
    backend = get_backend()
    return backend.encode_key
