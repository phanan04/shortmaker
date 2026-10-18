"""Public web fetches are size-limited, redirects validated, proxy env ignored."""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from .errors import NeedsInput, ProviderError


def public_addresses(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise NeedsInput("Only public HTTP/HTTPS URLs on standard ports are supported")
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError as exc:
        raise ProviderError(f"Cannot resolve source host: {p.hostname}") from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise NeedsInput("Private/local network URLs cannot be fetched as sources")
    return p, list(dict.fromkeys(a[4][0] for a in addresses))


def public_url(url):
    public_addresses(url)
    return url


def fetch(url, max_bytes=2_000_000):
    with httpx.Client(timeout=httpx.Timeout(20, connect=8), trust_env=False,
                      headers={"User-Agent": "TuTienShorts/0.1 (local research tool)"}) as client:
        for _ in range(5):
            parsed, addresses = public_addresses(url)
            # Pin the validated IP. A second DNS resolution could otherwise rebind a public
            # hostname to localhost/private space between validation and the actual request.
            address = addresses[0]
            netloc = f"[{address}]" if ":" in address else address
            if parsed.port:
                netloc += f":{parsed.port}"
            target = parsed._replace(netloc=netloc).geturl()
            try:
                with client.stream("GET", target, headers={"Host": parsed.netloc},
                                   extensions={"sni_hostname": parsed.hostname}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    if int(response.headers.get("content-length", "0")) > max_bytes:
                        raise NeedsInput("Remote file exceeds the download limit")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise NeedsInput("Remote file exceeds the download limit")
                    return bytes(data), response.headers.get("content-type", ""), url
            except httpx.HTTPError as exc:
                raise ProviderError(f"Source download failed: {type(exc).__name__}") from exc
    raise NeedsInput("Too many redirects")
