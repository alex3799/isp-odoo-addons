from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mikrotik_max_promesa_days = fields.Integer(
        string="Max Payment Promise Days",
        default=30,
        config_parameter="mikrotik.max_promesa_days",
        help="Maximum number of days allowed for a payment promise.",
    )

    def action_open_mikrotik_routers(self):
        """Open the MikroTik router list from the Settings page."""
        return {
            "type": "ir.actions.act_window",
            "name": "MikroTik Routers",
            "res_model": "mikrotik.router",
            "view_mode": "list,kanban,form",
            "target": "current",
        }
