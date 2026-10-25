#!/usr/bin/env python3
"""SAMPLE SIZE - APNs push sender (token-based .p8 auth).

Reads the device token from data/push.json (written by the app on first launch)
and sends a notification via Apple Push Notification service.

Env: APNS_KEY (.p8 contents), APNS_KEY_ID, APNS_TEAM_ID, APNS_ENV=sandbox|prod.
Dev builds installed via Xcode use the SANDBOX environment (default).

Usage: python push.py "Title" "Body"
Importable: from push import send_push
"""
import os
import sys
import json
import time
import pathlib

TOPIC = "com.sanaa.samplesize"
ENV = (os.environ.get("APNS_ENV") or "sandbox").lower()
HOST = "api.push.apple.com" if ENV == "prod" else "api.sandbox.push.apple.com"


def device_token():
    try:
        return json.loads(pathlib.Path("data/push.json").read_text()).get("token")
    except Exception:  # noqa: BLE001
        return None


def normalize_p8(raw):
    """Rebuild a clean PEM from the .p8 secret regardless of how its
    newlines/whitespace survived being pasted into a GitHub secret."""
    import re
    raw = (raw or "").strip().strip('"').strip("'").replace("\\n", "\n")
    m = re.search(r"-----BEGIN [A-Z ]+-----(.+?)-----END", raw, re.S)
    if not m:
        return raw  # let it fail loudly with the original if markers missing
    body = re.sub(r"\s+", "", m.group(1))
    wrapped = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----\n"


def _jwt():
    import jwt  # PyJWT; needs cryptography for ES256
    key = normalize_p8(os.environ["APNS_KEY"])
    kid = os.environ["APNS_KEY_ID"]
    team = os.environ["APNS_TEAM_ID"]
    return jwt.encode({"iss": team, "iat": int(time.time())},
                      key, algorithm="ES256", headers={"kid": kid, "alg": "ES256"})


def send_push(title, body, token=None):
    import httpx
    tok = token or device_token()
    if not tok:
        print("no device token in data/push.json — open the app once to register")
        return 1
    try:
        auth = _jwt()
    except Exception as e:  # noqa: BLE001
        print("jwt/auth error:", e)
        return 1
    url = f"https://{HOST}/3/device/{tok}"
    headers = {"authorization": f"bearer {auth}", "apns-topic": TOPIC,
               "apns-push-type": "alert", "apns-priority": "10"}
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    try:
        with httpx.Client(http2=True, timeout=20) as c:
            r = c.post(url, headers=headers, json=payload)
        print(f"APNs {r.status_code} {r.text[:200]} (env={ENV})")
        return 0 if r.status_code == 200 else 2
    except Exception as e:  # noqa: BLE001
        print("send error:", e)
        return 1


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "SAMPLE SIZE"
    b = sys.argv[2] if len(sys.argv) > 2 else "Push is live — your phone will buzz for injuries, finals, and bet windows."
    sys.exit(send_push(t, b))
