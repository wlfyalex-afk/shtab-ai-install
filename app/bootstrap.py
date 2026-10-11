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


def valid_text(value):
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in value)


def required(prompt, maximum=200):
    while True:
        value = input(prompt).strip()
        if value and len(value) <= maximum and valid_text(value):
            return value
        print(f"Нужно непустое значение до {maximum} символов без управляющих знаков и ошибок кодировки. Повторите ввод.")


def credentials():
    organization = required("Название организации: ")
    display_name = required("Имя первого администратора: ")
    while True:
        username = required("Логин (латиница, цифры, _, -): ", 40).lower()
        if re.fullmatch(r"[a-z][a-z0-9_-]{2,39}", username):
            break
        print("Логин: от 3 до 40 символов, начинается с латинской буквы. Повторите ввод.")
    while True:
        timezone = input("Часовой пояс [Asia/Vladivostok]: ").strip() or "Asia/Vladivostok"
        if not valid_text(timezone):
            print("Ошибка кодировки часового пояса. Повторите ввод или нажмите Enter.")
            continue
        try:
            ZoneInfo(timezone)
            break
        except (ZoneInfoNotFoundError, ValueError):
            print("Неизвестный часовой пояс. Повторите ввод или нажмите Enter для Asia/Vladivostok.")
    while True:
        password = getpass.getpass("Пароль (не менее 14 символов): ")
        if not 14 <= len(password) <= 200 or not valid_text(password):
            print("Нужен пароль от 14 до 200 символов без управляющих знаков и ошибок кодировки. Повторите ввод.")
            continue
        if password == getpass.getpass("Повторите пароль: "):
            break
        print("Пароли не совпали. Введите пароль заново.")
    return organization, display_name, username, timezone, password


def main():
    config_path = Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json"))
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        organization, display_name, username, timezone, password = credentials()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Создание администратора отменено. Данные не изменены; повторите shtabctl finish.")

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

