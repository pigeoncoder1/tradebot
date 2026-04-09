import base64
import json
from dataclasses import dataclass

import pyotp
import requests


class Roblox2FAError(Exception):
    pass


@dataclass
class ChallengeContext:
    challenge_id: str
    metadata_b64: str


@dataclass
class RobloxAuthenticator:
    user_id: int
    otp_secret: str
    roblosecurity_cookie: str

    @staticmethod
    def _get_csrf(resp: requests.Response) -> str | None:
        return resp.headers.get("x-csrf-token") or resp.headers.get("X-CSRF-TOKEN")

    @staticmethod
    def _b64decode_json(data: str) -> dict:
        padded = data + "=" * (-len(data) % 4)
        raw = base64.b64decode(padded).decode("utf-8")
        return json.loads(raw)

    @staticmethod
    def _b64encode_json(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    def current_code(self) -> str:
        return pyotp.TOTP(self.otp_secret).now()

    def attach_cookie(self, session: requests.Session) -> None:
        session.cookies.set(
            ".ROBLOSECURITY",
            self.roblosecurity_cookie,
            domain=".roblox.com",
        )

    def ensure_csrf_token(self, session: requests.Session, url: str) -> str:
        resp = session.post(url, json={})
        token = self._get_csrf(resp)
        if not token:
            raise Roblox2FAError(
                f"Could not obtain X-CSRF-TOKEN. status={resp.status_code}, body={resp.text}"
            )
        session.headers["X-CSRF-TOKEN"] = token
        return token

    @staticmethod
    def is_challenge_required(resp: requests.Response) -> bool:
        return (
            resp.status_code == 403
            and bool(resp.headers.get("rblx-challenge-id"))
            and bool(resp.headers.get("rblx-challenge-metadata"))
        )

    def solve_challenge(self, session: requests.Session, resp: requests.Response) -> ChallengeContext:
        challenge_id = resp.headers.get("rblx-challenge-id")
        metadata_b64 = resp.headers.get("rblx-challenge-metadata")

        if not challenge_id or not metadata_b64:
            raise Roblox2FAError("Missing challenge headers on 403 response.")

        decoded = self._b64decode_json(metadata_b64)

        inner_challenge_id = decoded.get("challengeId")
        action_type = decoded.get("actionType") or "Generic"
        remember_device = bool(decoded.get("rememberDevice", False))

        if not inner_challenge_id:
            raise Roblox2FAError(
                f"Could not find inner challengeId in metadata: {decoded}"
            )

        verify_url = (
            f"https://twostepverification.roblox.com/v1/users/"
            f"{self.user_id}/challenges/authenticator/verify"
        )
        verify_payload = {
            "actionType": action_type,
            "challengeId": inner_challenge_id,
            "code": self.current_code(),
        }

        verify_resp = session.post(verify_url, json=verify_payload)

        csrf = self._get_csrf(verify_resp)
        if csrf:
            session.headers["X-CSRF-TOKEN"] = csrf

        if not verify_resp.ok:
            raise Roblox2FAError(
                f"2FA verify failed: status={verify_resp.status_code}, body={verify_resp.text}"
            )

        verify_json = verify_resp.json()
        verification_token = verify_json.get("verificationToken")
        if not verification_token:
            raise Roblox2FAError(
                f"verificationToken missing in verify response: {verify_json}"
            )

        continued_metadata = {
            "rememberDevice": remember_device,
            "actionType": action_type,
            "verificationToken": verification_token,
            "challengeId": inner_challenge_id,
        }

        continue_url = "https://apis.roblox.com/challenge/v1/continue"
        continue_payload = {
            "challengeId": challenge_id,
            "challengeType": "twostepverification",
            "challengeMetadata": json.dumps(continued_metadata, separators=(",", ":")),
        }

        continue_resp = session.post(continue_url, json=continue_payload)

        csrf = self._get_csrf(continue_resp)
        if csrf:
            session.headers["X-CSRF-TOKEN"] = csrf

        if not continue_resp.ok:
            raise Roblox2FAError(
                f"Challenge continue failed: status={continue_resp.status_code}, body={continue_resp.text}"
            )
        print("[DEBUG] Decoded challenge metadata:", decoded)
        return ChallengeContext(
            challenge_id=challenge_id,
            metadata_b64=self._b64encode_json(continued_metadata),
        )

    @staticmethod
    def apply_challenge_headers(session: requests.Session, ctx: ChallengeContext) -> None:
        session.headers["rblx-challenge-id"] = ctx.challenge_id
        session.headers["rblx-challenge-type"] = "twostepverification"
        session.headers["rblx-challenge-metadata"] = ctx.metadata_b64