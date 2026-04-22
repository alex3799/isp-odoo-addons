import logging

from odoo import models, fields

from .smartolt_api import smartolt_api_call, smartolt_notification

_logger = logging.getLogger(__name__)


class SmartoltOnuType(models.Model):
    _name = "smartolt.onu.type"
    _description = "SmartOLT ONU Type"
    _rec_name = "name"
    _order = "name"

    smartolt_id = fields.Char(string="SmartOLT ID", index=True, copy=False)
    name = fields.Char(string="ONU Type Name", required=True)
    pon_type = fields.Selection(
        [("gpon", "GPON"), ("epon", "EPON")],
        string="PON Type",
    )
    capability = fields.Char(string="Capability")
    ethernet_ports = fields.Integer(string="Ethernet Ports", default=0)
    wifi_ports = fields.Integer(string="WiFi Ports", default=0)
    voip_ports = fields.Integer(string="VoIP Ports", default=0)
    catv = fields.Boolean(string="CATV Supported", default=False)
    allow_custom_profiles = fields.Boolean(string="Allow Custom Profiles", default=False)

    _sql_constraints = [
        ("smartolt_id_uniq", "unique(smartolt_id)", "SmartOLT ID must be unique."),
    ]

    def sync_from_smartolt(self):
        """Fetch all ONU types from SmartOLT and create/update local records."""
        data = smartolt_api_call(self.env, "system/get_onu_types")

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
                "name": item.get("name") or sid,
                "pon_type": item.get("pon_type") if item.get("pon_type") in ("gpon", "epon") else False,
                "capability": item.get("capability"),
                "ethernet_ports": int(item.get("ethernet_ports") or 0),
                "wifi_ports": int(item.get("wifi_ports") or 0),
                "voip_ports": int(item.get("voip_ports") or 0),
                "catv": str(item.get("catv", "0")) == "1",
                "allow_custom_profiles": str(item.get("allow_custom_profiles", "0")) == "1",
            }

            existing = self.search([("smartolt_id", "=", sid)], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1

        _logger.info("SmartOLT ONU Type sync: %d created, %d updated.", created, updated)
        return smartolt_notification(
            "ONU Types Synced", f"{created} created, {updated} updated."
        )
