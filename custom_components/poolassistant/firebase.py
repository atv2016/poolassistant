import aiohttp
import logging
from datetime import datetime, timedelta, UTC

_LOGGER = logging.getLogger(__name__)


class FirebaseAuth:
    def __init__(self, session, api_key, email, password):
        self._session = session
        self.api_key = api_key
        self.email = email
        self.password = password
        self.id_token = None
        self.refresh_token = None
        self.expires = None
        self.local_id = None

    async def login(self):
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={self.api_key}"
        payload = {"email": self.email, "password": self.password, "returnSecureToken": True}
        async with self._session.post(url, json=payload) as resp:
            data = await resp.json()
        if resp.status != 200:
            raise Exception(data)
        self.id_token = data["idToken"]
        self.refresh_token = data["refreshToken"]
        self.local_id = data["localId"]
        self.expires = datetime.now(UTC) + timedelta(seconds=int(data["expiresIn"]) - 60)
        _LOGGER.debug("Logged into Firebase")

    async def _refresh(self):
        # Note: this endpoint returns snake_case keys, unlike signInWithPassword.
        url = f"https://securetoken.googleapis.com/v1/token?key={self.api_key}"
        payload = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        async with self._session.post(url, data=payload) as resp:
            data = await resp.json()
        if resp.status != 200:
            raise Exception(data)
        self.id_token = data["id_token"]
        self.refresh_token = data["refresh_token"]
        self.expires = datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]) - 60)
        _LOGGER.debug("Refreshed Firebase token")

    async def get_token(self):
        if self.id_token is None:
            await self.login()
        elif datetime.now(UTC) >= self.expires:
            try:
                await self._refresh()
            except Exception:
                _LOGGER.warning("Token refresh failed, logging in again")
                await self.login()
        return self.id_token
