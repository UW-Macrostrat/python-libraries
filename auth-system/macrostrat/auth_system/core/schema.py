import enum
from typing import List
import datetime
from sqlalchemy import ForeignKey, func, DateTime, Enum, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import VARCHAR, TEXT, INTEGER, ARRAY, BOOLEAN, JSON, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class GroupMembers(Base):
    __tablename__ = "group_members"
    __table_args__ = {
        'schema': 'macrostrat_auth'
    }
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("macrostrat_auth.group.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("macrostrat_auth.user.id"))


class Group(Base):
    __tablename__ = "group"
    __table_args__ = {
        'schema': 'macrostrat_auth'
    }
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(VARCHAR(255))
    users: Mapped[List["User"]] = relationship(secondary="macrostrat_auth.group_members", lazy="joined",
                                               back_populates="groups")


class User(Base):
    __tablename__ = "user"
    __table_args__ = {
        'schema': 'macrostrat_auth'
    }
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sub: Mapped[str] = mapped_column(VARCHAR(255))
    name: Mapped[str] = mapped_column(VARCHAR(255))
    email: Mapped[str] = mapped_column(VARCHAR(255))
    groups: Mapped[List[Group]] = relationship(secondary="macrostrat_auth.group_members", lazy="joined",
                                               back_populates="users")
    created_on: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_on: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Token(Base):
    __tablename__ = "token"
    __table_args__ = {
        'schema': 'macrostrat_auth'
    }
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(VARCHAR(255), unique=True)
    group: Mapped[Group] = mapped_column(ForeignKey("macrostrat_auth.group.id"))
    used_on: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_on: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True)
    )
    created_on: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


