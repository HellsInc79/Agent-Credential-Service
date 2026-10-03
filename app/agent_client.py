"""Small renewable-token client for long-running local agents."""

import time
from urllib.parse import quote

import requests


class AgentPlatformClient:
    """Use an agent API key once and renew short-lived JWTs automatically.

    The API key stays in process memory. The short-lived access token is cached
    in memory and refreshed before expiry, so an active agent can keep calling
    the service without a fixed token-count ceiling.
    """

    def __init__(self, base_url, api_key, timeout=600, session=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self._access_token = None
        self._refresh_at = 0.0
        self._token_lifetime = 0

    def _refresh_token(self):
        try:
            response = self.session.post(
                f"{self.base_url}/v1/tokens",
                json={"api_key": self.api_key},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            if exc.response is not None:
                try:
                    detail = exc.response.json().get("detail", "")
                except ValueError:
                    detail = exc.response.text[:300]
            raise RuntimeError(detail or f"Could not get an access token from {self.base_url}.") from exc
        data = response.json()
        self._access_token = data["access_token"]
        self._token_lifetime = max(1, int(data.get("expires_in", 1800)))
        self._refresh_at = time.monotonic() + self._token_lifetime * 0.75

    def request(self, method, path, **kwargs):
        if not self._access_token or time.monotonic() >= self._refresh_at:
            self._refresh_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.session.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach the agent platform at {self.base_url}.") from exc
        if response.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                response = self.session.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            except requests.RequestException as exc:
                raise RuntimeError(f"Could not reach the agent platform at {self.base_url}.") from exc
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = response.json().get("detail", response.text[:500])
            except ValueError:
                detail = response.text[:500]
            raise RuntimeError(f"Platform request failed ({response.status_code}): {detail}") from exc
        return response.json()

    def chat(self, prompt, agent_id, room_name="executive-board"):
        return self.request(
            "POST",
            "/v1/chat",
            json={"prompt": prompt, "agent_id": agent_id, "room_name": room_name},
        )

    def orchestrate(self, prompt, room_name="executive-board", controller_id=None):
        payload = {"prompt": prompt, "room_name": room_name}
        if controller_id:
            payload["controller_id"] = controller_id
        return self.request("POST", "/v1/orchestrate", json=payload)

    def post_message(self, content, room_name="executive-board"):
        return self.request(
            "POST",
            f"/v1/rooms/{quote(room_name, safe='')}/messages",
            json={"content": content, "as_user": True},
        )

    def close(self):
        if hasattr(self.session, "close"):
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
