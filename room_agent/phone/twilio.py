"""Twilio's REST API, used directly (no SDK): place a call, point Jarvis's number at Jarvis, check request signatures."""

import base64
import hashlib
import hmac
import logging

import requests

log = logging.getLogger("room-agent")
API = "https://api.twilio.com/2010-04-01"


class TwilioError(Exception):
    pass


class Twilio:
    def __init__(self, sid, token, http=requests):
        self.sid, self.token, self.http = sid, token, http

    def _post(self, path, data):
        r = self.http.post(f"{API}/Accounts/{self.sid}/{path}", data=data, auth=(self.sid, self.token), timeout=15)
        if r.status_code not in (200, 201):
            raise TwilioError(f"http_{r.status_code}")  # (never the body: keep account details out of the logs)
        return r.json()

    def call(self, to, from_, url):
        """Ring `to` from Jarvis's number; when they answer, Twilio asks `url` what to do. -> call sid"""
        return self._post("Calls.json", {"To": to, "From": from_, "Url": url, "Method": "POST"})["sid"]

    def set_voice_webhook(self, number, url):
        """Calls to Jarvis's number go to `url`."""
        r = self.http.get(f"{API}/Accounts/{self.sid}/IncomingPhoneNumbers.json", params={"PhoneNumber": number},
                          auth=(self.sid, self.token), timeout=15)
        if r.status_code != 200:
            raise TwilioError(f"http_{r.status_code}")
        numbers = r.json().get("incoming_phone_numbers", [])
        if not numbers:
            raise TwilioError("that number isn't in this Twilio account")
        self._post(f"IncomingPhoneNumbers/{numbers[0]['sid']}.json", {"VoiceUrl": url, "VoiceMethod": "POST"})
        return numbers[0]["sid"]


def signature(url, params, token):
    """Twilio's X-Twilio-Signature: HMAC-SHA1 of the URL + the POST parameters sorted by name, base64."""
    data = url + "".join(k + str(v) for k, v in sorted((params or {}).items()))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def valid(url, params, token, sig):
    return bool(token and sig) and hmac.compare_digest(signature(url, params, token), sig)


def same_number(a, b):
    digits = lambda n: "".join(ch for ch in str(n or "") if ch.isdigit())[-10:]
    return bool(digits(a)) and digits(a) == digits(b)
