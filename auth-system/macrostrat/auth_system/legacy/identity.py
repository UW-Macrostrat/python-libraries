from werkzeug.security import generate_password_hash, check_password_hash


class IdentityProvider:
    def create_user(self, username, password):
        ...

    def get_user(self, username):
        ...


# Abstract base class for all models
class BaseUser:
    hashed_password: str
    username: str

    def __init__(self, username, password):
        self.username = username
        self.set_password(password)

    def set_password(self, plaintext):
        from .context import get_secret_key

        # 'salt' the passwords to prevent brute forcing
        salt = get_secret_key()
        self.hashed_password = generate_password_hash(salt + str(plaintext))

    def is_correct_password(self, plaintext):
        from .context import get_secret_key

        salt = get_secret_key()
        return check_password_hash(self.hashed_password, salt + str(plaintext))
