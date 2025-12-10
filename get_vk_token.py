#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
get_vk_token.py

Утилита для получения пользовательского VK access_token / refresh_token
через VK ID (OAuth 2.1 + PKCE).

Делает за тебя всю грязную работу:
  * генерит code_verifier и code_challenge (PKCE),
  * печатает ссылку авторизации,
  * просит вставить URL из адресной строки после "Разрешить",
  * сам вытаскивает code и device_id,
  * меняет code на access_token и refresh_token.

Дальше полученный access_token можно использовать как VK_USER_TOKEN
для твоего бота, который постит в группы и грузит фотки.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
from dataclasses import dataclass
from typing import Dict, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

AUTH_URL = "https://id.vk.com/authorize"
TOKEN_URL = "https://id.vk.com/oauth2/auth"
DEFAULT_REDIRECT_URI = "https://oauth.vk.com/blank.html"
DEFAULT_SCOPE = "wall,photos,groups"


@dataclass
class VkAppConfig:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scope: str = DEFAULT_SCOPE
    state: str = "vk_cli_state_12345"


def base64url_encode(data: bytes) -> str:
    """Base64 URL-safe без '=' в конце."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_code_verifier(length: int = 64) -> str:
    """
    Сгенерировать code_verifier по RFC 7636 (43–128 символов).

    Берём случайные байты, кодируем в base64url, обрезаем/дополняем до нужной длины.
    """
    if length < 43 or length > 128:
        raise ValueError("length must be between 43 and 128")

    raw = secrets.token_bytes(length)
    verifier = base64url_encode(raw)

    if len(verifier) < 43:
        verifier = verifier + "A" * (43 - len(verifier))
    elif len(verifier) > 128:
        verifier = verifier[:128]

    return verifier


def generate_code_challenge(verifier: str) -> str:
    """PKCE S256: code_challenge = BASE64URL(SHA256(verifier))."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64url_encode(digest)


def get_config_from_env() -> VkAppConfig:
    """
    Берём настройки из окружения.

    Обязательное:
      VK_OAUTH_CLIENT_ID

    Необязательные:
      VK_OAUTH_SCOPE            (по умолчанию wall,photos,groups)
      VK_OAUTH_REDIRECT_URI     (по умолчанию https://oauth.vk.com/blank.html)
      VK_OAUTH_STATE            (любой маркер для CSRF, можно не трогать)
    """
    client_id = os.getenv("VK_OAUTH_CLIENT_ID")
    if not client_id:
        print(
            "Ошибка: не задан VK_OAUTH_CLIENT_ID.\n"
            "Задай его в .env или окружении, это ID приложения VK ID.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    scope = os.getenv("VK_OAUTH_SCOPE", DEFAULT_SCOPE)
    redirect_uri = os.getenv("VK_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    state = os.getenv("VK_OAUTH_STATE", "vk_cli_state_12345")

    return VkAppConfig(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
    )


def build_authorize_url(cfg: VkAppConfig, code_challenge: str) -> str:
    """Собрать ссылку авторизации VK ID с PKCE."""
    params: Dict[str, str] = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "scope": cfg.scope,
        "redirect_uri": cfg.redirect_uri,
        "state": cfg.state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def parse_redirect_url(url: str) -> Tuple[str, str]:
    """
    Разобрать URL, который пользователь скопировал из адресной строки после авторизации.

    Ищем там параметры:
      code       — authorization code (10 минут жизни)
      device_id  — нужен VK при обмене на access_token

    Параметры могут быть как в query, так и во fragment — учитываем оба варианта.
    """
    parsed = urlparse(url.strip())

    qs = parse_qs(parsed.query)
    fs = parse_qs(parsed.fragment) if parsed.fragment else {}

    def get_param(name: str) -> str | None:
        if name in qs and qs[name]:
            return qs[name][0]
        if name in fs and fs[name]:
            return fs[name][0]
        return None

    code = get_param("code")
    device_id = get_param("device_id")

    if not code:
        raise ValueError("В URL не найден параметр 'code'. Убедись, что скопировал всю строку полностью.")
    if not device_id:
        raise ValueError("В URL не найден параметр 'device_id'. Скопируй адресную строку целиком и повтори.")

    return code, device_id


def exchange_code_for_token(
    cfg: VkAppConfig,
    code: str,
    device_id: str,
    code_verifier: str,
) -> Dict[str, str]:
    """
    Обменять authorization code на access_token / refresh_token.

    POST https://id.vk.com/oauth2/auth
      grant_type=authorization_code
      code=...
      redirect_uri=...
      client_id=...
      device_id=...
      code_verifier=...
      state=...
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "device_id": device_id,
        "code_verifier": code_verifier,
        "state": cfg.state,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    resp.raise_for_status()
    token_data = resp.json()

    if "error" in token_data:
        raise RuntimeError(
            f"VK вернул ошибку при обмене кода: "
            f"{token_data.get('error')} — {token_data.get('error_description')}"
        )

    return token_data


def main() -> int:
    print("=== VK ID PKCE helper ===\n")

    load_dotenv()
    cfg = get_config_from_env()

    print("Настройки приложения VK ID:")
    print(f"  client_id    = {cfg.client_id}")
    print(f"  redirect_uri = {cfg.redirect_uri}")
    print(f"  scope        = {cfg.scope}")
    print()

    # 1) Генерим PKCE-пару
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # 2) Собираем ссылку авторизации
    auth_url = build_authorize_url(cfg, code_challenge)

    print("1) Открой в браузере вот эту ссылку (скопируй целиком):\n")
    print(auth_url)
    print(
        "\n2) Залогинься, если попросят, и нажми «Разрешить» / «Продолжить».\n"
        "3) Когда появится страница с текстом вроде:\n"
        '   «Пожалуйста, не копируйте данные из адресной строки…»\n'
        "   — СКОПИРУЙ ПОЛНОСТЬЮ адрес из строки браузера (с https://... и до самого конца)\n"
        "   и вставь его сюда.\n"
    )

    try:
        redirect_url = input("Вставь сюда URL после авторизации и нажми Enter:\n> ").strip()
        if not redirect_url:
            print("Пустой ввод, нечего парсить.", file=sys.stderr)
            return 1

        code, device_id = parse_redirect_url(redirect_url)

        print("\nНашёл в URL:")
        print(f"  code      = {code}")
        print(f"  device_id = {device_id}")
        print("\nОбмениваю code на access_token...\n")

        token_data = exchange_code_for_token(
            cfg=cfg,
            code=code,
            device_id=device_id,
            code_verifier=code_verifier,
        )

    except Exception as e:
        print(f"\nОшибка: {e}", file=sys.stderr)
        return 1

    # Красиво выводим что дали
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    print("=== Успешно! ===\n")
    if access_token:
        print(f"ACCESS TOKEN:\n  {access_token}\n")
    if refresh_token:
        print(f"REFRESH TOKEN:\n  {refresh_token}\n")
    if expires_in is not None:
        print(f"Срок действия access_token (сек): {expires_in}")
        if expires_in == 0:
            print("  (0 обычно означает долгоживущий токен)")
        print()

    print("Можешь положить токен в .env, например:\n")
    if access_token:
        print(f"VK_USER_TOKEN={access_token}")
    if refresh_token:
        print(f"VK_REFRESH_TOKEN={refresh_token}")
    print()

    print("WARNING: Никому не показывай этот токен и URL из адресной строки, это доступ к твоему аккаунту.")
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
