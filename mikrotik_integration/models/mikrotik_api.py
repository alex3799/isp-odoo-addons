import logging
import ssl

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth

from odoo.exceptions import UserError

# RouterOS may use anonymous DH (no certificate) — suppress urllib3 warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_logger = logging.getLogger(__name__)

# Cipher string that allows MikroTik's anonymous DH (ADH) ciphers while still
# supporting normal cert-based ciphers for routers that have proper certs.
_MIKROTIK_CIPHERS = "aNULL:ALL:@SECLEVEL=0"


class _MikroTikSSLAdapter(HTTPAdapter):
    """HTTPAdapter that allows anonymous-DH ciphers used by RouterOS.

    RouterOS ships with no server certificate by default and uses ADH ciphers
    (e.g. ADH-AES256-SHA256).  Python's ssl module and urllib3 v2 reject these
    by default because there is no peer authentication.  We explicitly allow
    them here since we are already disabling certificate verification, and set
    assert_hostname=False so urllib3's secondary hostname check also passes.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers(_MIKROTIK_CIPHERS)
        kwargs["ssl_context"] = ctx
        kwargs["assert_hostname"] = False
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers(_MIKROTIK_CIPHERS)
        proxy_kwargs["ssl_context"] = ctx
        proxy_kwargs["assert_hostname"] = False
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _get_router_session(router):
    """Create a requests.Session configured for a MikroTik router.

    Uses HTTP Basic Auth (username:password).
    SSL verification is disabled; anonymous-DH ciphers are allowed because
    RouterOS ships without a server certificate by default.
    """
    session = requests.Session()
    session.auth = HTTPBasicAuth(router.username, router.password or "")
    session.verify = False
    session.mount("https://", _MikroTikSSLAdapter())
    return session


def mikrotik_rest_call(router, method, endpoint, data=None, timeout=30):
    """Generic REST call to a MikroTik router's RouterOS REST API.

    RouterOS REST conventions (different from standard REST):
      GET    → read / query
      PUT    → create a new entry
      PATCH  → update an existing entry
      DELETE → remove an entry
      POST   → execute a command / action

    URL structure: {scheme}://{host}:{port}/rest/{endpoint}

    Returns parsed JSON (RouterOS responds directly with JSON, no envelope).
    Sets router.status='online' on success, 'offline' or 'error' on failure.
    Raises UserError with a human-friendly message on any failure.
    """
    scheme = "https" if router.use_ssl else "http"
    url = f"{scheme}://{router.host}:{router.port}/rest/{endpoint.lstrip('/')}"
    method = method.upper()
    session = _get_router_session(router)

    _logger.info("MikroTik REST %s %s (router: %s)", method, url, router.name)

    try:
        response = session.request(
            method=method,
            url=url,
            json=data if data is not None else None,
            timeout=timeout,
        )
    except requests.exceptions.SSLError as e:
        router.sudo().write({"status": "error"})
        raise UserError(
            f"SSL error connecting to {router.name} ({router.host}). "
            f"Verify SSL settings or disable SSL if the router does not support it: {e}"
        ) from e
    except requests.exceptions.ConnectTimeout:
        router.sudo().write({"status": "offline"})
        raise UserError(
            f"Connection timed out to {router.name} ({router.host}:{router.port}). "
            f"Check that the router is reachable and the port is correct."
        )
    except requests.exceptions.ConnectionError as e:
        router.sudo().write({"status": "offline"})
        raise UserError(
            f"Cannot connect to {router.name} ({router.host}:{router.port}). "
            f"Check that the host/port is correct and the router is online: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        router.sudo().write({"status": "offline"})
        raise UserError(f"Request to {router.name} failed: {e}") from e

    if response.status_code == 401:
        router.sudo().write({"status": "error"})
        raise UserError(
            f"Authentication failed for {router.name} ({router.host}). "
            f"Check the username and password."
        )

    if response.status_code == 403:
        router.sudo().write({"status": "error"})
        raise UserError(
            f"Access denied on {router.name} ({router.host}). "
            f"The API user may lack the required permissions."
        )

    if not response.ok:
        router.sudo().write({"status": "error"})
        try:
            err = response.json()
            detail = err.get("detail", response.text)
        except ValueError:
            detail = response.text or response.reason
        raise UserError(
            f"MikroTik API error {response.status_code} on {router.name}: {detail}"
        )

    router.sudo().write({"status": "online"})

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as e:
        raise UserError(
            f"Invalid JSON response from {router.name}: {e}"
        ) from e


def mikrotik_test_connection(router):
    """Test connectivity and retrieve system information from a MikroTik router.

    Calls:
      GET /rest/system/resource  → hardware/resource stats
      GET /rest/system/identity  → router hostname/identity

    Returns a dict with keys:
      identity, version, board-name, uptime, cpu-load, free-memory, total-memory

    Raises UserError with a friendly message on any failure.
    """
    resource = mikrotik_rest_call(router, "GET", "system/resource", timeout=15)
    identity = mikrotik_rest_call(router, "GET", "system/identity", timeout=15)

    return {
        "identity": identity.get("name", ""),
        "version": resource.get("version", ""),
        "board-name": resource.get("board-name", ""),
        "uptime": resource.get("uptime", ""),
        "cpu-load": resource.get("cpu-load", 0),
        "free-memory": resource.get("free-memory", 0),
        "total-memory": resource.get("total-memory", 0),
    }


def mikrotik_notification(title, message, ntype="success", sticky=False):
    """Return an Odoo client notification action dict."""
    return {
        "type": "ir.actions.client",
        "tag": "display_notification",
        "params": {
            "title": title,
            "message": message,
            "type": ntype,
            "sticky": sticky,
        },
    }
