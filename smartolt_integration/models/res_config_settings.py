from odoo import models, fields
from odoo.exceptions import UserError

from .smartolt_api import smartolt_api_call, smartolt_notification


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    smartolt_api_url = fields.Char(
        string="SmartOLT API URL",
        config_parameter="smartolt.api_url",
        help="Base URL of your SmartOLT instance, e.g. https://smartolt.example.com",
    )
    smartolt_api_key = fields.Char(
        string="SmartOLT API Key",
        config_parameter="smartolt.api_key",
        help="X-Token API key from your SmartOLT account.",
    )

    def action_test_connection(self):
        """Test the SmartOLT API connection by fetching OLTs."""
        try:
            data = smartolt_api_call(self.env, "system/get_olts")
            olts = data.get("response", [])
            count = len(olts) if isinstance(olts, list) else 0
            return smartolt_notification(
                "Connection Successful",
                f"SmartOLT API is reachable. Found {count} OLT(s).",
            )
        except UserError as e:
            return smartolt_notification(
                "Connection Failed",
                str(e),
                ntype="danger",
                sticky=True,
            )

    def action_sync_olts(self):
        return self.env["smartolt.olt"].sync_from_smartolt()

    def action_sync_onu_types(self):
        return self.env["smartolt.onu.type"].sync_from_smartolt()

    def action_sync_zones(self):
        return self.env["smartolt.zone"].sync_from_smartolt()

    def action_sync_speed_profiles(self):
        return self.env["smartolt.speed.profile"].sync_from_smartolt()

    def action_sync_all_onus(self):
        return self.env["smartolt.onu"].sync_from_smartolt()
