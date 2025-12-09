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
  python vk_group_bot.py --message "Текст поста"

Опционально:
  python vk_group_bot.py --message-file post.txt
"""

from dotenv import load_dotenv
import argparse
import os
import sys
from typing import Any, Dict

import requests

API_BASE_URL = "https://api.vk.com/method"
API_VERSION = "5.131"


class VkApiError(RuntimeError):
    """Ошибка, которую вернул VK API."""


def vk_request(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
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


def post_to_group_wall(
    group_id: int,
    token: str,
    message: str,
) -> int:
    """
    Добавить запись на стену сообщества.

    group_id: числовой ID без минуса (12345678)
    token: токен сообщества
    message: текст поста

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

    response = vk_request("wall.post", params)
    return response.get("post_id", -1)


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
        description="Постинг в стену сообщества ВК через токен сообщества."
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

    try:
        post_id = post_to_group_wall(group_id=group_id, token=token, message=message)
    except VkApiError as e:
        print(f"VK API вернул ошибку: {e}", file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print(f"Сетевая ошибка при обращении к VK API: {e}", file=sys.stderr)
        return 1

    print(f"Пост успешно создан: https://vk.com/wall-{group_id}_{post_id}")
    return 0


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(main())
