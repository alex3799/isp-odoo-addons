import logging

from odoo import models, fields

from .smartolt_api import smartolt_api_call, smartolt_notification

_logger = logging.getLogger(__name__)


class SmartoltOlt(models.Model):
    _name = "smartolt.olt"
    _description = "SmartOLT OLT"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"

    name = fields.Char(string="OLT Name", required=True, tracking=True)
    hardware_version = fields.Char(string="Model / Hardware Version", tracking=True)
    ip_address = fields.Char(string="IP Address", tracking=True)
    telnet_port = fields.Char(string="Telnet Port")
    snmp_port = fields.Char(string="SNMP Port")
    smartolt_id = fields.Char(string="SmartOLT ID", copy=False, index=True)
    last_sync = fields.Datetime(string="Last Sync", readonly=True)

    _sql_constraints = [
        ("smartolt_id_uniq", "unique(smartolt_id)", "SmartOLT ID must be unique."),
    ]

    def sync_from_smartolt(self):
        """Fetch all OLTs from SmartOLT and create/update local records."""
        data = smartolt_api_call(self.env, "system/get_olts")

        olts = data.get("response", [])
        if not isinstance(olts, list):
            olts = []

        now = fields.Datetime.now()
        created = updated = 0

        for item in olts:
            smartolt_id = str(item.get("id", ""))
            if not smartolt_id:
                continue

            vals = {
                "name": item.get("name") or smartolt_id,
                "hardware_version": item.get("olt_hardware_version"),
                "ip_address": item.get("ip"),
                "telnet_port": str(item.get("telnet_port", "")),
                "snmp_port": str(item.get("snmp_port", "")),
                "smartolt_id": smartolt_id,
                "last_sync": now,
            }

            existing = self.search([("smartolt_id", "=", smartolt_id)], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1

        _logger.info("SmartOLT OLT sync: %d created, %d updated.", created, updated)
        return smartolt_notification(
            "SmartOLT OLT Sync Complete",
            f"{created} OLTs created, {updated} updated.",
        )
