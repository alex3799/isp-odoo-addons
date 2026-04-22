import logging
import requests

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _get_api_config(env):
    """Read SmartOLT API config from ir.config_parameter."""
    ICP = env["ir.config_parameter"].sudo()
    base_url = ICP.get_param(
        "smartolt.api_url", "https://your-tenant.smartolt.com"
    ).rstrip("/")
    api_key = ICP.get_param("smartolt.api_key", "")
    if not api_key:
        raise UserError(
            "SmartOLT API Key must be configured in Settings before syncing."
        )
    return base_url, api_key


def _handle_response(response):
    """Parse response JSON and handle API-level errors."""
    try:
        data = response.json()
    except ValueError as e:
        raise UserError(f"Invalid JSON response from SmartOLT: {e}") from e

    if isinstance(data, dict) and data.get("status") is False:
        error_code = data.get("error_code", "")
        error_msg = data.get("error", "Unknown API error")
        if error_code == "forbidden" and "hourly limit" in error_msg.lower():
            raise UserError(
                "SmartOLT API hourly rate limit reached. Please wait and try again later."
            )
        if "read" in error_msg.lower() and "only" in error_msg.lower():
            raise UserError(
                "Your SmartOLT API key is read-only and cannot perform this action. "
                "Please use an API key with write permissions."
            )
        raise UserError(f"SmartOLT API error: {error_msg}")

    return data


def smartolt_api_call(env, endpoint, timeout=120):
    """GET request to SmartOLT API."""
    base_url, api_key = _get_api_config(env)
    headers = {"X-Token": api_key}
    url = f"{base_url}/api/{endpoint}"

    try:
        response = requests.get(
            url, headers=headers, timeout=timeout, allow_redirects=True
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise UserError(f"SmartOLT API request failed: {e}") from e

    return _handle_response(response)


def smartolt_api_post(env, endpoint, data=None, timeout=120):
    """POST request to SmartOLT API."""
    base_url, api_key = _get_api_config(env)
    headers = {"X-Token": api_key}
    url = f"{base_url}/api/{endpoint}"

    try:
        response = requests.post(
            url, headers=headers, json=data, timeout=timeout, allow_redirects=True
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # Try to parse error body for friendly message
        try:
            err_data = e.response.json()
            err_msg = err_data.get("error", str(e))
            if "read" in err_msg.lower() and "only" in err_msg.lower():
                raise UserError(
                    "Your SmartOLT API key is read-only and cannot perform this action. "
                    "Please use an API key with write permissions."
                ) from e
            raise UserError(f"SmartOLT API error: {err_msg}") from e
        except (ValueError, AttributeError):
            raise UserError(f"SmartOLT API request failed: {e}") from e
    except requests.exceptions.RequestException as e:
        raise UserError(f"SmartOLT API request failed: {e}") from e

    return _handle_response(response)


def smartolt_notification(title, message, ntype="success", sticky=False):
    """Return an Odoo client notification action."""
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
