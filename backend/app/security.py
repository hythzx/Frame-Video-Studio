"""URL safety checks for user-supplied media URLs and yt-dlp network requests.

Application-level validation is defense in depth, NOT a replacement for
container-level outbound firewall rules (DNS rebinding and external tools).
"""
import ipaddress
import socket
from urllib.parse import urlsplit

_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".test", ".invalid")
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "host.docker.internal"}
_RESERVED_SUFFIXES = (".example",)


def validate_media_url(value: str, *, resolve: bool = False, allow_http: bool = False) -> str:
    """Reject non-Web URLs, network credentials, IP literals, and private DNS.

    resolve=True checks ALL DNS addresses for a hostname; callers should invoke
    this before every yt-dlp request. Local egress filtering is still needed to
    protect against DNS changes between validation and socket connection.
    """
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("媒体地址无效")
    parts = urlsplit(value.strip())
    if parts.scheme not in (("http", "https") if allow_http else ("https",)):
        raise ValueError("仅支持 HTTPS 媒体页面链接")
    if not parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError("媒体地址无效")
    try:
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port
    except (ValueError, UnicodeError) as exc:
        raise ValueError("媒体主机名无效") from exc
    if not host or host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES + _RESERVED_SUFFIXES):
        raise ValueError("禁止访问本机或内部网络地址")
    if host.endswith(".onion") or host.startswith("localhost.") or "." not in host:
        raise ValueError("仅允许公网网站域名")
    if any(not label or len(label) > 63 for label in host.split(".")):
        raise ValueError("域名格式无效")
    if port is not None and (port < 1 or port > 65535):
        raise ValueError("端口无效")
    # Disallow IP literals, including IPv6, alternative IP spellings, and
    # numeric hostnames accepted as IPv4 by some networking stacks.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("不允许直接访问 IP 地址")
    if host.lower().startswith(("0x",)) or all(
        part.isdigit() or part.lower().startswith("0x") for part in host.split(".")):
        raise ValueError("不允许使用数值形式的 IP 地址")
    if resolve:
        try:
            records = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                         type=socket.SOCK_STREAM)
        except (OSError, ValueError) as exc:
            raise ValueError("无法解析媒体主机名") from exc
        if not records or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in records
        ):
            raise ValueError("目标地址指向内部或非公网网络")
    return value.strip()
