import logging

from odoo import models, fields

from .smartolt_api import smartolt_api_call, smartolt_notification

_logger = logging.getLogger(__name__)


class SmartoltZone(models.Model):
    _name = "smartolt.zone"
    _description = "SmartOLT Zone"
    _rec_name = "name"
    _order = "name"

    smartolt_id = fields.Char(string="SmartOLT ID", index=True, copy=False)
    name = fields.Char(string="Zone Name", required=True)

    _sql_constraints = [
        ("smartolt_id_uniq", "unique(smartolt_id)", "SmartOLT ID must be unique."),
    ]

    def sync_from_smartolt(self):
        """Fetch all zones from SmartOLT and create/update local records."""
        data = smartolt_api_call(self.env, "system/get_zones")

        items = data.get("response", [])
        if not isinstance(items, list):
            items = []

        created = updated = 0
        for item in items:
            sid = str(item.get("id", ""))
            if not sid:
                continue

            vals = {
                "smartolt_id": sid,
                "name": (item.get("name") or sid).strip(),
            }

            existing = self.search([("smartolt_id", "=", sid)], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1

        _logger.info("SmartOLT Zone sync: %d created, %d updated.", created, updated)
        return smartolt_notification(
            "Zones Synced", f"{created} created, {updated} updated."
        )
