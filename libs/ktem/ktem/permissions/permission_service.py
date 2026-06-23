from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from sqlalchemy import delete
from sqlmodel import Session, select
from theflow.settings import settings as flowsettings
from tzlocal import get_localzone

from ktem.db.engine import engine

from .models import SourcePermission

PermissionLevel = Literal["read", "owner"]

PUBLIC_PRINCIPAL_TYPE = "public"
PUBLIC_PRINCIPAL_ID = "*"
USER_PRINCIPAL_TYPE = "user"
READ_PERMISSIONS = {"read", "owner"}
WRITE_PERMISSIONS = {"owner"}


@dataclass(frozen=True)
class Principal:
    type: str
    id: str


def resolve_principal(user_id: str | int | None) -> Principal:
    if isinstance(user_id, Principal):
        return user_id
    normalized = str(user_id or "").strip()
    if normalized:
        return Principal(USER_PRINCIPAL_TYPE, normalized)
    if not getattr(flowsettings, "KH_FEATURE_USER_MANAGEMENT", False):
        return Principal(USER_PRINCIPAL_TYPE, "default")
    return Principal(PUBLIC_PRINCIPAL_TYPE, PUBLIC_PRINCIPAL_ID)


def _source_owner(source: Any) -> str:
    return str(getattr(source, "user", "") or "default")


def _index_id(index: Any) -> int:
    return int(getattr(index, "id", getattr(index, "index_id", 0)))


def _source_table(index: Any) -> Any:
    resources = getattr(index, "_resources", None) or {}
    return resources.get("Source") or getattr(index, "Source")


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    if hasattr(config, "get"):
        return config.get(key, default)
    if hasattr(config, key):
        return getattr(config, key)
    return default


def _is_private_index(index: Any) -> bool:
    config = getattr(index, "config", {}) or {}
    return bool(_config_value(config, "private", getattr(index, "private", False)))


def _source_permissions(index_id: int, source_id: str) -> list[SourcePermission]:
    with Session(engine) as session:
        statement = select(SourcePermission).where(
            SourcePermission.index_id == index_id,
            SourcePermission.source_id == source_id,
        )
        return session.exec(statement).all()


def _matching_permissions(
    permissions: Sequence[SourcePermission], principal: Principal
) -> list[SourcePermission]:
    principals = [
        (principal.type, principal.id),
        (PUBLIC_PRINCIPAL_TYPE, PUBLIC_PRINCIPAL_ID),
    ]
    return [
        row
        for row in permissions
        if (row.principal_type, row.principal_id) in principals
    ]


def ensure_default_acl(index: Any, source: Any, owner_id: str | None = None) -> None:
    """Create the initial ACL for a source without changing legacy source tables."""
    source_id = str(source.id)
    owner_id = str(owner_id or _source_owner(source))
    index_id = _index_id(index)

    with Session(engine) as session:
        existing = session.exec(
            select(SourcePermission.id).where(
                SourcePermission.index_id == index_id,
                SourcePermission.source_id == source_id,
            )
        ).first()
        if existing:
            return

        now = datetime.now(get_localzone())
        rows = [
            SourcePermission(
                index_id=index_id,
                source_id=source_id,
                principal_type=USER_PRINCIPAL_TYPE,
                principal_id=owner_id,
                permission="owner",
                created_by=owner_id,
                date_created=now,
                date_updated=now,
            )
        ]
        if not _is_private_index(index):
            rows.append(
                SourcePermission(
                    index_id=index_id,
                    source_id=source_id,
                    principal_type=PUBLIC_PRINCIPAL_TYPE,
                    principal_id=PUBLIC_PRINCIPAL_ID,
                    permission="read",
                    created_by=owner_id,
                    date_created=now,
                    date_updated=now,
                )
            )
        session.add_all(rows)
        session.commit()


def can_read_source(index: Any, source: Any, principal: Principal | str | None) -> bool:
    principal = (
        resolve_principal(principal)
        if not isinstance(principal, Principal)
        else principal
    )
    source_id = str(source.id)

    permissions = _source_permissions(_index_id(index), source_id)
    matching = _matching_permissions(permissions, principal)
    if any(row.permission in READ_PERMISSIONS for row in matching):
        return True
    if permissions:
        return False

    # Legacy fallback before backfill: preserve previous private/public behavior.
    if _is_private_index(index):
        return principal.type == USER_PRINCIPAL_TYPE and principal.id == _source_owner(source)
    return True


def can_write_source(index: Any, source: Any, principal: Principal | str | None) -> bool:
    principal = (
        resolve_principal(principal)
        if not isinstance(principal, Principal)
        else principal
    )
    permissions = _source_permissions(_index_id(index), str(source.id))
    matching = _matching_permissions(permissions, principal)
    if any(row.permission in WRITE_PERMISSIONS for row in matching):
        return True
    if permissions:
        return False

    # Legacy owner fallback keeps old sources editable by their original uploader.
    return principal.type == USER_PRINCIPAL_TYPE and principal.id == _source_owner(source)


def filter_source_ids(
    index: Any,
    source_ids: Sequence[str | None],
    principal: Principal | str | None,
) -> list[str]:
    principal = (
        resolve_principal(principal)
        if not isinstance(principal, Principal)
        else principal
    )
    normalized = [str(source_id) for source_id in source_ids if source_id]
    if not normalized:
        return []

    Source = _source_table(index)
    allowed: list[str] = []
    with Session(engine) as session:
        rows = session.execute(select(Source).where(Source.id.in_(normalized))).all()
        source_by_id = {str(source.id): source for (source,) in rows}

    for source_id in normalized:
        source = source_by_id.get(source_id)
        if source is not None and can_read_source(index, source, principal):
            allowed.append(source_id)
    return allowed


def source_filter_reason(
    index: Any,
    source_id: str,
    principal: Principal | str | None,
) -> str:
    """Return a non-sensitive reason summary for an ACL-filtered source."""
    principal = (
        resolve_principal(principal)
        if not isinstance(principal, Principal)
        else principal
    )
    Source = _source_table(index)
    with Session(engine) as session:
        row = session.execute(
            select(Source).where(Source.id == str(source_id))
        ).first()
    if row is None:
        return "source_not_found"

    source = row[0]
    permissions = _source_permissions(_index_id(index), str(source_id))
    if not permissions:
        if _is_private_index(index):
            return "private_index_owner_only"
        return "legacy_public_allowed"
    matching = _matching_permissions(permissions, principal)
    if not matching:
        return "no_matching_acl_principal"
    if not any(row.permission in READ_PERMISSIONS for row in matching):
        return "matching_acl_without_read"
    if not can_read_source(index, source, principal):
        return "read_denied"
    return "allowed"


def list_source_permissions(index: Any, source_id: str) -> list[SourcePermission]:
    with Session(engine) as session:
        return session.exec(
            select(SourcePermission).where(
                SourcePermission.index_id == _index_id(index),
                SourcePermission.source_id == str(source_id),
            )
        ).all()


def grant_source_permission(
    index: Any,
    source_id: str,
    principal: Principal,
    permission: PermissionLevel,
    actor_id: str | None = None,
) -> SourcePermission:
    now = datetime.now(get_localzone())
    with Session(engine) as session:
        existing = session.exec(
            select(SourcePermission).where(
                SourcePermission.index_id == _index_id(index),
                SourcePermission.source_id == str(source_id),
                SourcePermission.principal_type == principal.type,
                SourcePermission.principal_id == principal.id,
            )
        ).one_or_none()
        if existing:
            existing.permission = permission
            existing.date_updated = now
            row = existing
        else:
            row = SourcePermission(
                index_id=_index_id(index),
                source_id=str(source_id),
                principal_type=principal.type,
                principal_id=principal.id,
                permission=permission,
                created_by=actor_id,
                date_created=now,
                date_updated=now,
            )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def set_source_acl(
    index: Any,
    source: Any,
    actor_id: str,
    entries: Sequence[dict[str, str]],
) -> list[SourcePermission]:
    if not can_write_source(index, source, actor_id):
        raise PermissionError("Only source owners can update file permissions.")

    owner_id = _source_owner(source)
    normalized: dict[tuple[str, str], PermissionLevel] = {
        (USER_PRINCIPAL_TYPE, owner_id): "owner"
    }
    for entry in entries:
        principal_type = (entry.get("principalType") or entry.get("principal_type") or "").strip()
        principal_id = (entry.get("principalId") or entry.get("principal_id") or "").strip()
        permission = (entry.get("permission") or "read").strip()
        if principal_type == PUBLIC_PRINCIPAL_TYPE:
            principal_id = PUBLIC_PRINCIPAL_ID
        if principal_type not in {USER_PRINCIPAL_TYPE, PUBLIC_PRINCIPAL_TYPE}:
            continue
        if not principal_id:
            continue
        if permission not in READ_PERMISSIONS:
            continue
        normalized[(principal_type, principal_id)] = permission  # type: ignore[assignment]

    with Session(engine) as session:
        session.execute(
            delete(SourcePermission).where(
                SourcePermission.index_id == _index_id(index),
                SourcePermission.source_id == str(source.id),
            )
        )
        now = datetime.now(get_localzone())
        rows = [
            SourcePermission(
                index_id=_index_id(index),
                source_id=str(source.id),
                principal_type=principal_type,
                principal_id=principal_id,
                permission=permission,
                created_by=actor_id,
                date_created=now,
                date_updated=now,
            )
            for (principal_type, principal_id), permission in normalized.items()
        ]
        session.add_all(rows)
        session.commit()
        return list_source_permissions(index, str(source.id))
