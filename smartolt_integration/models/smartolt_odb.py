import logging

from odoo import models, fields, api
from .smartolt_api import smartolt_api_call, smartolt_notification

_logger = logging.getLogger(__name__)


class SmartoltOdb(models.Model):
    _name = "smartolt.odb"
    _description = "SmartOLT ODB"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    external_id = fields.Integer(string="SmartOLT ID", index=True, copy=False)
    lat = fields.Float(string="Latitude", digits=(10, 7))
    lng = fields.Float(string="Longitude", digits=(10, 7))
    zone_id = fields.Many2one("smartolt.zone", string="Zone", ondelete="set null")
    status = fields.Selection(
        [("active", "Active"), ("inactive", "Inactive")],
        string="Status",
        default="active",
    )
    last_sync = fields.Datetime(string="Last Sync", readonly=True)

    _sql_constraints = [
        (
            "external_id_uniq",
            "unique(external_id)",
            "SmartOLT external ID must be unique.",
        ),
    ]

    def sync_from_smartolt(self):
        """Sync ODBs from SmartOLT for all zones."""
        zones = self.env["smartolt.zone"].search([("smartolt_id", "!=", False)])
        if not zones:
            return smartolt_notification(
                "No Zones Found",
                "No zones with a SmartOLT ID found. Sync zones first.",
                ntype="warning",
            )

        created = updated = 0
        now = fields.Datetime.now()

        for zone in zones:
            try:
                data = smartolt_api_call(
                    self.env, f"system/get_odbs/{zone.smartolt_id}"
                )
            except Exception as e:
                _logger.warning(
                    "Failed to fetch ODBs for zone %s (%s): %s",
                    zone.name,
                    zone.smartolt_id,
                    e,
                )
                continue

            items = data.get("response", [])
            if not isinstance(items, list):
                items = []

            for item in items:
                ext_id = item.get("id")
                if not ext_id:
                    continue

                vals = {
                    "name": (item.get("name") or str(ext_id)).strip(),
                    "external_id": int(ext_id),
                    "lat": float(item.get("latitude") or 0.0),
                    "lng": float(item.get("longitude") or 0.0),
                    "zone_id": zone.id,
                    "status": "active" if item.get("status", 1) else "inactive",
                    "last_sync": now,
                }

                existing = self.search([("external_id", "=", int(ext_id))], limit=1)
                if existing:
                    existing.write(vals)
                    updated += 1
                else:
                    self.create(vals)
                    created += 1

        _logger.info("SmartOLT ODB sync: %d created, %d updated.", created, updated)
        return smartolt_notification(
            "ODBs Synced", f"{created} created, {updated} updated."
        )
