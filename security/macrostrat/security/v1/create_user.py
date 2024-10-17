import click
from click import echo, secho, style, prompt
from sqlalchemy.exc import IntegrityError

from werkzeug.security import generate_password_hash, check_password_hash
from os import environ
from macrostrat.database.mapper import BaseModel


class User(BaseModel):
    if BaseModel.loaded_from_cache:
        __table__ = BaseModel.metadata.tables["user"]
    else:
        __tablename__ = "user"
        __table_args__ = {"extend_existing": True}

    # Columns are automagically mapped from database
    # *NEVER* directly set the password column.

    def set_password(self, plaintext):
        # 'salt' the passwords to prevent brute forcing
        salt = environ.get("SPARROW_SECRET_KEY")
        self.password = generate_password_hash(salt + str(plaintext))

    def is_correct_password(self, plaintext):
        salt = environ.get("SPARROW_SECRET_KEY")
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
