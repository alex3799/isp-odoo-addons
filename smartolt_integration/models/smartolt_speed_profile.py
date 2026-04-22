import logging

from odoo import models, fields

from .smartolt_api import smartolt_api_call, smartolt_notification

_logger = logging.getLogger(__name__)


class SmartoltSpeedProfile(models.Model):
    _name = "smartolt.speed.profile"
    _description = "SmartOLT Speed Profile"
    _rec_name = "name"
    _order = "name"

    smartolt_id = fields.Char(string="SmartOLT ID", index=True, copy=False)
    name = fields.Char(string="Profile Name", required=True)
    speed = fields.Char(string="Speed (kbps)")
    direction = fields.Selection(
        [("upload", "Upload"), ("download", "Download")],
        string="Direction",
    )
    profile_type = fields.Selection(
        [("internet", "Internet"), ("iptv", "IPTV")],
        string="Profile Type",
    )

    _sql_constraints = [
        ("smartolt_id_uniq", "unique(smartolt_id)", "SmartOLT ID must be unique."),
    ]

    def sync_from_smartolt(self):
        """Fetch all speed profiles from SmartOLT and create/update local records."""
        data = smartolt_api_call(self.env, "system/get_speed_profiles")

        items = data.get("response", [])
        if not isinstance(items, list):
            items = []

        created = updated = 0
        for item in items:
            sid = str(item.get("id", ""))
            if not sid:
                continue

            direction = item.get("direction")
            if direction not in ("upload", "download"):
                direction = False

            profile_type = item.get("type")
            if profile_type not in ("internet", "iptv"):
                profile_type = False

            vals = {
                "smartolt_id": sid,
                "name": item.get("name") or sid,
                "speed": item.get("speed"),
                "direction": direction,
                "profile_type": profile_type,
            }

            existing = self.search([("smartolt_id", "=", sid)], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1

        _logger.info("SmartOLT Speed Profile sync: %d created, %d updated.", created, updated)
        return smartolt_notification(
            "Speed Profiles Synced", f"{created} created, {updated} updated."
        )
