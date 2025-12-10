#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
vk_group_bot.py

Бот для постинга и редактирования постов на стене сообщества ВК.

Переменные окружения:

  VK_GROUP_ID        - числовой ID группы (без минуса, например 12345678)
  VK_GROUP_TOKEN     - токен сообщества (можно только для текстовых постов)
  VK_USER_TOKEN      - ТЕПЕРЬ ЭТО REFRESH TOKEN (vk2.a....) от VK ID
  VK_OAUTH_CLIENT_ID - ID приложения VK ID (через которое получали токены)
  VK_OAUTH_CLIENT_SECRET - секрет этого приложения

Логика:

  * Если есть VK_GROUP_TOKEN и пост без картинок, можем постить только им.
  * Если нужно грузить картинки ИЛИ нет group-токена:
      - по VK_USER_TOKEN (refresh_token) берём свежий access_token
        через https://id.vk.com/oauth2/auth (grant_type=refresh_token)
      - этим access_token вызываем photos.* и wall.*

  * Если VK при refresh выдаёт новый refresh_token, скрипт печатает его
    в stderr и просит обновить VK_USER_TOKEN в .env.
"""

from dotenv import load_dotenv
import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

API_BASE_URL = "https://api.vk.com/method"
API_VERSION = "5.131"
VK_OAUTH_TOKEN_URL = "https://id.vk.com/oauth2/auth"


class VkApiError(RuntimeError):
    """Ошибка VK API или OAuth VK ID."""


def vk_request(method: str, params: Dict[str, Any]) -> Any:
    """
    Вызвать метод VK API и вернуть поле 'response' или кинуть VkApiError.

    params ДОЛЖЕН уже содержать access_token и v.
    """
    url = f"{API_BASE_URL}/{method}"
    resp = requests.post(url, data=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        err = data["error"]
        raise VkApiError(
            f"VK API error {err.get('error_code')}: {err.get('error_msg')}"
        )

    return data["response"]


def _load_binary_from_source(source: str) -> bytes:
    """
    Загрузить байты файла из локального пути или URL.

    Если source начинается с http:// или https://, считаем его URL.
    Иначе — локальный путь.
    """
    if source.startswith(("http://", "https://")):
        r = requests.get(source, timeout=30)
        r.raise_for_status()
        return r.content

    if not os.path.exists(source):
        raise FileNotFoundError(f"Файл не найден: {source}")
    with open(source, "rb") as f:
        return f.read()


def upload_photos_for_wall(
    group_id: int,
    access_token: str,
    image_sources: List[str],
) -> List[str]:
    """
    Загрузить изображения и вернуть список строк вложений вида 'photo<owner_id>_<id>'.

    image_sources — список локальных путей или URL.
    access_token — ПОЛЬЗОВАТЕЛЬСКИЙ access_token (НЕ токен сообщества!)
    """
    if not image_sources:
        return []

    # Важно: photos.getWallUploadServer работает только с user access token
    upload_server = vk_request(
        "photos.getWallUploadServer",
        {
            "group_id": group_id,
            "access_token": access_token,
            "v": API_VERSION,
        },
    )
    upload_url = upload_server["upload_url"]

    attachments: List[str] = []

    for src in image_sources:
        data = _load_binary_from_source(src)

        files = {"photo": ("image.jpg", data)}
        resp = requests.post(upload_url, files=files, timeout=60)
        resp.raise_for_status()
        upload_result = resp.json()

        if not all(k in upload_result for k in ("photo", "server", "hash")):
            raise VkApiError(f"Неожиданный ответ сервера загрузки фото: {upload_result}")

        saved_photos = vk_request(
            "photos.saveWallPhoto",
            {
                "group_id": group_id,
                "photo": upload_result["photo"],
                "server": upload_result["server"],
                "hash": upload_result["hash"],
                "access_token": access_token,
                "v": API_VERSION,
            },
        )

        if not saved_photos:
            raise VkApiError("VK API не вернул данные о сохранённом фото.")

        photo_obj = saved_photos[0]
        owner_id = photo_obj["owner_id"]
        photo_id = photo_obj["id"]
        attachments.append(f"photo{owner_id}_{photo_id}")

    return attachments


def post_to_group_wall(
    group_id: int,
    token: str,
    message: str,
    attachments: Optional[List[str]] = None,
) -> int:
    """
    Добавить запись на стену сообщества.

    token: может быть group-токеном или user access_token.
    attachments: список строк вида 'photo<owner_id>_<id>'

    Возвращает post_id.
    """
    owner_id = -group_id
    params: Dict[str, Any] = {
        "owner_id": owner_id,
        "from_group": 1,
        "message": message,
        "access_token": token,
        "v": API_VERSION,
    }
    if attachments:
        params["attachments"] = ",".join(attachments)

    response = vk_request("wall.post", params)
    return response.get("post_id", -1)


def edit_group_wall_post(
    group_id: int,
    token: str,
    post_id: int,
    message: str,
    attachments: Optional[List[str]] = None,
) -> int:
    """
    Отредактировать существующий пост на стене сообщества.

    token: лучше использовать user access_token, но часто работает и group.
    """
    owner_id = -group_id
    params: Dict[str, Any] = {
        "owner_id": owner_id,
        "post_id": post_id,
        "message": message,
        "access_token": token,
        "v": API_VERSION,
    }
    if attachments:
        params["attachments"] = ",".join(attachments)

    response = vk_request("wall.edit", params)
    # Обычно возвращает 1, так что на всякий случай вернём исходный post_id
    return response.get("post_id", post_id)


def read_message_from_args(args: argparse.Namespace) -> str:
    """Получить текст поста из аргумента --message или --message-file."""
    if args.message and args.message_file:
        raise ValueError("Используй либо --message, либо --message-file, но не оба сразу.")

    if args.message:
        return args.message

    if args.message_file:
        path = args.message_file
        if not os.path.exists(path):
            raise FileNotFoundError(f"Файл с текстом не найден: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    raise ValueError("Нужно указать --message или --message-file.")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Постинг и редактирование постов в стене сообщества ВК.",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="Текст поста (одной строкой).",
    )
    parser.add_argument(
        "-f",
        "--message-file",
        help="Путь к файлу с текстом поста (UTF-8).",
    )
    parser.add_argument(
        "-i",
        "--image",
        action="append",
        dest="images",
        help="Путь к картинке или URL (можно указать несколько раз).",
    )
    parser.add_argument(
        "-a",
        "--attachment",
        action="append",
        dest="attachments",
        help=(
            "Готовая строка вложения VK, например 'photo-12345678_987654321'. "
            "Можно указать несколько раз."
        ),
    )
    parser.add_argument(
        "--edit",
        type=int,
        help="Редактировать существующий пост с указанным post_id вместо создания нового.",
    )
    return parser.parse_args(argv)


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> Tuple[str, Optional[str], Optional[int]]:
    """
    Обновить access_token по refresh_token через VK ID.

    POST https://id.vk.com/oauth2/auth
      grant_type=refresh_token
      refresh_token=...
      client_id=...
      client_secret=...

    Возвращает (access_token, new_refresh_token_or_None, expires_in_or_None).
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    resp = requests.post(VK_OAUTH_TOKEN_URL, data=data, headers=headers, timeout=15)
    resp.raise_for_status()
    token_data = resp.json()

    if "error" in token_data:
        raise VkApiError(
            f"VK OAuth error {token_data.get('error')}: "
            f"{token_data.get('error_description')}"
        )

    access_token = token_data.get("access_token")
    if not access_token:
        raise VkApiError("VK OAuth не вернул access_token при обновлении по refresh_token.")

    new_refresh = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    return access_token, new_refresh, expires_in


def main(argv=None) -> int:
    args = parse_args(argv)

    group_id_str = os.getenv("VK_GROUP_ID")
    group_token = os.getenv("VK_GROUP_TOKEN")  # токен сообщества (опционально)

    # ВНИМАНИЕ: VK_USER_TOKEN = REFRESH TOKEN
    refresh_token = os.getenv("VK_USER_TOKEN", None)

    oauth_client_id = os.getenv("VK_OAUTH_CLIENT_ID")
    oauth_client_secret = os.getenv("VK_OAUTH_CLIENT_SECRET")

    if not group_id_str:
        print(
            "Ошибка: не задан VK_GROUP_ID.\n"
            "Создай .env или выставь переменные окружения, например:\n"
            "  export VK_GROUP_ID=12345678\n",
            file=sys.stderr,
        )
        return 1

    try:
        group_id = int(group_id_str)
        if group_id <= 0:
            raise ValueError
    except ValueError:
        print(
            f"Ошибка: VK_GROUP_ID должен быть положительным числом, сейчас: {group_id_str!r}",
            file=sys.stderr,
        )
        return 1

    try:
        message = read_message_from_args(args).strip()
        if not message:
            print("Ошибка: текст поста пустой.", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Ошибка при чтении текста поста: {e}", file=sys.stderr)
        return 1

    attachments: List[str] = []

    # 1) Уже готовые attachment-строки
    if args.attachments:
        attachments.extend(args.attachments)

    # user_access_token будем получать по мере необходимости по refresh_token
    user_access_token: Optional[str] = os.getenv("VK_ACCESS_TOKEN", None)

    def ensure_user_access_token(reason: str) -> str:
        """
        Гарантировать наличие user access_token.

        Если его ещё нет — берём по refresh_token.
        """
        nonlocal user_access_token, refresh_token

        if user_access_token:
            return user_access_token

        if not refresh_token:
            raise VkApiError(
                f"Нужен VK_USER_TOKEN (refresh_token), чтобы {reason}. "
                f"Сейчас VK_USER_TOKEN не задан."
            )
        if not oauth_client_id or not oauth_client_secret:
            raise VkApiError(
                f"Нужны VK_OAUTH_CLIENT_ID и VK_OAUTH_CLIENT_SECRET, чтобы {reason}. "
                f"Сейчас один из них не задан."
            )

        access_token, new_refresh, expires_in = refresh_access_token(
            refresh_token=refresh_token,
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
        )
        user_access_token = access_token

        if new_refresh and new_refresh != refresh_token:
            # Сообщим пользователю, что refresh_token обновился.
            print(
                "ВНИМАНИЕ: VK выдал новый refresh_token. "
                "Обнови VK_USER_TOKEN в своём .env на значение ниже:\n"
                f"{new_refresh}\n",
                file=sys.stderr,
            )
            # Можем сразу начать использовать новый refresh в рамках этого запуска
            refresh_token = new_refresh

        return user_access_token

    # 2) Картинки, которые надо загрузить через API
    image_sources = args.images or []
    if image_sources or args.edit:
        access_token_for_upload = ensure_user_access_token("загрузить изображения в пост")

    if image_sources:
        try:
            uploaded = upload_photos_for_wall(
                group_id=group_id,
                access_token=access_token_for_upload,
                image_sources=image_sources,
            )
            attachments.extend(uploaded)
        except Exception as e:
            print(f"Ошибка при загрузке изображений: {e}", file=sys.stderr)
            return 1

    # 3) Выбираем токен для wall.post / wall.edit
    wall_token: Optional[str] = group_token if group_token else user_access_token

    if wall_token is None:
        # Нет токена сообщества — попробуем взять user access_token по refresh_token
        try:
            wall_token = ensure_user_access_token("вызвать wall.post / wall.edit")
        except Exception as e:
            print(
                "Ошибка: нет VK_GROUP_TOKEN и не удалось получить user access_token "
                "по VK_USER_TOKEN (refresh_token).\n"
                f"Детали: {e}",
                file=sys.stderr,
            )
            return 1

    if wall_token is None:
        print(
            "Ошибка: нет ни VK_GROUP_TOKEN, ни рабочего VK_USER_TOKEN (refresh_token). "
            "Невозможно вызвать wall.post / wall.edit.",
            file=sys.stderr,
        )
        return 1

    # 4) Собственно, постинг / редактирование
    try:
        if args.edit:
            post_id = edit_group_wall_post(
                group_id=group_id,
                token=access_token_for_upload if access_token_for_upload else wall_token,
                post_id=args.edit,
                message=message,
                attachments=attachments or None,
            )
            print(f"Пост успешно отредактирован: https://vk.com/wall-{group_id}_{post_id}")
        else:
            post_id = post_to_group_wall(
                group_id=group_id,
                token=wall_token,
                message=message,
                attachments=attachments or None,
            )
            print(f"Пост успешно создан: https://vk.com/wall-{group_id}_{post_id}")
    except VkApiError as e:
        print(f"VK API вернул ошибку: {e}", file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print(f"Сетевая ошибка при обращении к VK API: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(main())
