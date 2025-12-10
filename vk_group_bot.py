#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
vk_group_bot.py

Бот для постинга в стену сообщества ВК через VK API.
После единовременной настройки токена больше не требует логина в ВК.

Настройка:
  1) В группе: Управление -> Настройки -> Работа с API -> Ключи доступа -> Создать ключ.
  2) Скопировать токен и ID группы в переменные окружения:
       VK_GROUP_ID   - числовой ID группы (без минуса, например 12345678)
       VK_GROUP_TOKEN - строка токена сообщества

Использование:
  # Новый пост
  python vk_group_bot.py --message "Текст поста"

  # Новый пост с картинками
  python vk_group_bot.py -m "Текст" -i img1.jpg -i img2.png

  # Редактирование существующего поста
  python vk_group_bot.py --edit 123 --message "Новый текст" -i img1.jpg
"""

from dotenv import load_dotenv
import argparse
import os
import sys
from typing import Any, Dict, List

import requests

API_BASE_URL = "https://api.vk.com/method"
API_VERSION = "5.131"


class VkApiError(RuntimeError):
    """Ошибка, которую вернул VK API."""


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
    # локальный файл
    if not os.path.exists(source):
        raise FileNotFoundError(f"Файл не найден: {source}")
    with open(source, "rb") as f:
        return f.read()


def upload_photos_for_wall(
    group_id: int,
    token: str,
    image_sources: List[str],
) -> List[str]:
    """
    Загрузить изображения и вернуть список строк вложений вида 'photo<owner_id>_<id>'.

    image_sources — список локальных путей или URL.
    """
    if not image_sources:
        return []

    # Получаем URL для загрузки
    upload_server = vk_request(
        "photos.getWallUploadServer",
        {
            "group_id": group_id,
            "access_token": token,
            "v": API_VERSION,
        },
    )
    upload_url = upload_server["upload_url"]

    attachments: List[str] = []

    for src in image_sources:
        data = _load_binary_from_source(src)

        # Отправляем файл на upload_url
        files = {"photo": ("image.jpg", data)}
        resp = requests.post(upload_url, files=files, timeout=60)
        resp.raise_for_status()
        upload_result = resp.json()

        if not all(k in upload_result for k in ("photo", "server", "hash")):
            raise VkApiError(f"Неожиданный ответ сервера загрузки фото: {upload_result}")

        # Сохраняем фото в альбом сообщества
        saved_photos = vk_request(
            "photos.saveWallPhoto",
            {
                "group_id": group_id,
                "photo": upload_result["photo"],
                "server": upload_result["server"],
                "hash": upload_result["hash"],
                "access_token": token,
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
    attachments: List[str] | None = None,
) -> int:
    """
    Добавить запись на стену сообщества.

    group_id: числовой ID без минуса (12345678)
    token: токен сообщества
    message: текст поста
    attachments: список строк вида 'photo<owner_id>_<id>'

    Возвращает post_id.
    """
    owner_id = -group_id  # для групп owner_id = -group_id
    params: Dict[str, Any] = {
        "owner_id": owner_id,
        "from_group": 1,        # пост от имени сообщества
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
    attachments: List[str] | None = None,
) -> int:
    """
    Отредактировать существующий пост на стене сообщества.

    group_id: числовой ID без минуса (12345678)
    token: токен сообщества
    post_id: ID редактируемого поста
    message: новый текст
    attachments: список строк вида 'photo<owner_id>_<id>' (заменяют старые вложения)

    Возвращает post_id (обычно тот же).
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
    # wall.edit обычно возвращает 1, но на всякий случай пытаемся взять post_id
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
        description="Постинг и редактирование постов в стене сообщества ВК через токен сообщества.",
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
        "--edit",
        type=int,
        help="Редактировать существующий пост с указанным post_id вместо создания нового.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Берём настройки из окружения
    group_id_str = os.getenv("VK_GROUP_ID")
    token = os.getenv("VK_GROUP_TOKEN")

    if not group_id_str or not token:
        print(
            "Ошибка: не заданы VK_GROUP_ID и/или VK_GROUP_TOKEN.\n"
            "Создай .env или выставь переменные окружения, например:\n"
            "  export VK_GROUP_ID=12345678\n"
            "  export VK_GROUP_TOKEN=vk1.a.XXXX\n",
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

    # Готовим вложения (если есть картинки)
    try:
        image_sources = args.images or []
        attachments = (
            upload_photos_for_wall(
                group_id=group_id,
                token=token,
                image_sources=image_sources,
            )
            if image_sources
            else []
        )
    except Exception as e:
        print(f"Ошибка при загрузке изображений: {e}", file=sys.stderr)
        return 1

    try:
        if args.edit:
            post_id = edit_group_wall_post(
                group_id=group_id,
                token=token,
                post_id=args.edit,
                message=message,
                attachments=attachments or None,
            )
            print(f"Пост успешно отредактирован: https://vk.com/wall-{group_id}_{post_id}")
        else:
            post_id = post_to_group_wall(
                group_id=group_id,
                token=token,
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
