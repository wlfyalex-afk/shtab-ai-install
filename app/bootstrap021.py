#!/usr/bin/env python3
"""Create the first organization, person and administrator on an empty DB."""
import getpass
import json
import os
import re
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg2.extras import Json
from werkzeug.security import generate_password_hash

from store import Store


def required(prompt, maximum=200):
    value = input(prompt).strip()
    if not value or len(value) > maximum:
        raise SystemExit(f"Нужно непустое значение до {maximum} символов")
    return value


def main():
    config_path = Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json"))
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    organization = required("Название организации: ")
    display_name = required("Имя первого администратора: ")
    username = required("Логин (латиница, цифры, _, -): ", 40).lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,39}", username):
        raise SystemExit("Недопустимый логин")
    timezone = input("Часовой пояс [Asia/Vladivostok]: ").strip() or "Asia/Vladivostok"
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise SystemExit("Неизвестный часовой пояс")
    password = getpass.getpass("Пароль (не менее 14 символов): ")
    if not 14 <= len(password) <= 200:
        raise SystemExit("Нужен пароль от 14 до 200 символов")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпали")

    org_id, person_id, user_id = (str(uuid.uuid4()) for _ in range(3))
    store = Store(settings)
    with store.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(2002001)")
        cursor.execute("SELECT count(*) AS n FROM secretary_users")
        if cursor.fetchone()["n"]:
            raise SystemExit("Первый пользователь уже создан; bootstrap повторно запрещён")
        cursor.execute(
            "INSERT INTO organizations(id,name,timezone) VALUES (%s,%s,%s)",
            (org_id, organization, timezone),
        )
        cursor.execute(
            """INSERT INTO people(id,organization_id,display_name,timezone)
               VALUES (%s,%s,%s,%s)""",
            (person_id, org_id, display_name, timezone),
        )
        cursor.execute(
            """INSERT INTO secretary_users
               (id,username,person_id,password_hash,is_admin,app_role)
               VALUES (%s,%s,%s,%s,true,'secretary')""",
            (user_id, username, person_id, generate_password_hash(password)),
        )
        cursor.execute(
            """INSERT INTO audit_events
               (id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
               VALUES (%s,%s,'SYSTEM','INITIAL_ADMIN_CREATED','WEB_USER',%s,%s)""",
            (str(uuid.uuid4()), org_id, user_id, Json({"username": username})),
        )
    print("Первичная организация и администратор созданы.")


if __name__ == "__main__":
    main()

