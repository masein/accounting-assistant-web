"""The server only connects out to the public internet.

A rate feed or a statements mailbox is reached by the server, so its address
must not point inside the network the server sits in (the cloud metadata
service, the database, a router). Every address a name resolves to must be
public (``ip_address.is_global``). A caller that then connects should use the
address that was checked (``public_addresses``), not resolve the name again,
so a name that changes between the check and the connection can't slip
through.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable

Resolver = Callable[[str, int], list[str]]


def resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


class NotPublic(ValueError):
    """The host can't be resolved, or resolves to a private address."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code             # "resolve" | "private"


def public_addresses(host: str, port: int, *, resolver: Resolver | None = None) -> list[str]:
    """The host's addresses, all of them public — or ``NotPublic``."""
    try:
        addresses = (resolver or resolve)(host, port)
    except OSError as exc:
        raise NotPublic(f"can't resolve {host}", code="resolve") from exc
    if not addresses:
        raise NotPublic(f"can't resolve {host}", code="resolve")
    for addr in addresses:
        if not ipaddress.ip_address(addr.split("%", 1)[0]).is_global:
            raise NotPublic(f"{host} is not a public address", code="private")
    return addresses
