"""Explicit-domain Cloudflare DNS provisioning; no arbitrary HTTP connector execution."""
import ipaddress
import json
import re
import urllib.parse
import urllib.request


def provision(spec, token):
    dns = spec["dns"]
    kind = "AAAA" if ipaddress.ip_address(dns["address"]).version == 6 else "A"
    base = "https://api.cloudflare.com/client/v4/zones/" + dns["zone"] + "/dns_records"
    # Disable redirects so authorization can never leave the configured API origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())

    def request(url, method="GET", data=None):
        req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data else None,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=30) as response:
                body = json.loads(response.read(1024 * 1024))
            if not body.get("success"):
                raise RuntimeError("DNS provider rejected the operation")
            return body["result"]
        except Exception as exc:
            raise RuntimeError("DNS provisioning failed; verify the scoped zone token") from exc

    records = request(base + "?" + urllib.parse.urlencode({"name": spec["domain"], "per_page": 100}))
    records = [record for record in records if record.get("name") == spec["domain"]]
    if any(r.get("type") in {"CNAME", "A", "AAAA"} and r["type"] != kind for r in records):
        raise ValueError("Conflicting DNS address/CNAME exists; resolve it before automated provisioning")
    matches = [r for r in records if r.get("type") == kind]
    if len(matches) > 1:
        raise ValueError("Multiple DNS addresses exist; refusing to replace a load-balanced record set")
    body = {"type": kind, "name": spec["domain"], "content": dns["address"], "ttl": 300, "proxied": False}
    if matches:
        record = matches[0]
        if record["content"] == dns["address"] and not record.get("proxied"):
            return {"changed": False}
        identifier = record.get("id", "")
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid DNS record identifier")
        request(base + "/" + identifier, "PUT", body)
    else:
        request(base, "POST", body)
    return {"changed": True}
