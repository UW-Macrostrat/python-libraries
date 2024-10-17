from click import echo, style, prompt
from sqlalchemy.exc import IntegrityError

from werkzeug.security import generate_password_hash, check_password_hash
from os import environ

from macrostrat.auth_system.v1.context import get_secret_key
from macrostrat.database.mapper import BaseModel

# Abstract base class for all models
class BaseUser(BaseModel):
    __abstract__ = True
    password: str

    def set_password(self, plaintext: str) -> None:
        ...

    def is_correct_password(self, plaintext):
        ...


class User(BaseUser):

    def set_password(self, plaintext):
        # 'salt' the passwords to prevent brute forcing
        salt = get_secret_key()
        self.password = generate_password_hash(salt + str(plaintext))

    def is_correct_password(self, plaintext):
        salt =  get_secret_key()
        return check_password_hash(self.password, salt + str(plaintext))


def _create_user(db, username, password, raise_on_error=True):
    try:
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        assert user.is_correct_password(password)
        db.session.commit()
        return user
    except IntegrityError:
        db.session.rollback()
        if raise_on_error:
            raise


def create_user(db):
    username = prompt("Enter the desired username")
    name = "Username {}".format(style(username, fg="cyan", bold=True))
    while db.session.query(User).get(username) is not None:
        username = prompt(name + " is already taken. Choose another.")
    echo(name + " is available!")

    password = prompt("Create a password", hide_input=True, confirmation_prompt=True)
    _create_user(db, username, password)
    echo("Successfully created user and hashed password!")
