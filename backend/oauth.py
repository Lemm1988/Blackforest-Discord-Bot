"""Schlanke, manuelle Discord-OAuth2-Implementierung (Authorization Code Flow).

Keine zusätzliche Bibliothek nötig - nur zwei HTTP-Requests gegen Discords API.
Doku: https://docs.discord.com/developers/topics/oauth2
"""
from urllib.parse import urlencode

import requests

DISCORD_API = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
TOKEN_URL = f"{DISCORD_API}/oauth2/token"
USER_URL = f"{DISCORD_API}/users/@me"


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(TOKEN_URL, data=data, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_discord_user(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(USER_URL, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()
