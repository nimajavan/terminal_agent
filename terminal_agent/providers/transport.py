"""Loopback requests must neither follow redirects nor use environment proxies."""
import urllib.request
import json


def read_json_response(response):
    """Reject oversized or malformed envelopes before accessing provider fields."""
    raw = response.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("Provider response exceeds 1 MB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Provider response must be a JSON object")
    return data


def chat_response_text(data):
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Provider returned no valid response choices")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("Provider response message must contain text")
    return message["content"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def local_open(request, timeout):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)
