# Copyright 2024 Mundo1Telecom
# License LGPL-3.0 or later
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class MikrotikPromesaWizard(models.TransientModel):
    _name = "mikrotik.promesa.wizard"
    _description = "Grant Payment Promise"

    subscription_id = fields.Many2one(
        comodel_name="sale.subscription",
        string="Subscription",
        required=True,
    )
    promesa_date = fields.Date(
        string="Suspend On",
        required=True,
        default=lambda self: date.today() + relativedelta(days=3),
        help="Date on which the subscription will be suspended if unpaid.",
    )
    days_computed = fields.Integer(
        string="Days from Today",
        compute="_compute_days_computed",
        readonly=True,
    )
    note = fields.Text(string="Note")

    @api.depends("promesa_date")
    def _compute_days_computed(self):
        today = date.today()
        for rec in self:
            if rec.promesa_date:
                rec.days_computed = (rec.promesa_date - today).days
            else:
                rec.days_computed = 0

    def action_confirm(self):
        self.ensure_one()
        today = date.today()
        if not self.promesa_date or self.promesa_date <= today:
            raise UserError("Promise date must be in the future.")

        max_days = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("mikrotik.max_promesa_days", "30")
        )
        days = self.days_computed
        if days > max_days:
            raise UserError(
                f"Payment promise cannot exceed {max_days} days (requested: {days} days)."
            )

        sub = self.subscription_id

        # Re-enable PPP secret on the router BEFORE updating Odoo status.
        # action_enable() raises on router failure, which prevents the
        # isp_status write below — never update Odoo if the router didn't respond.
        if sub.ppp_secret_id and sub.ppp_secret_id.disabled:
            sub.ppp_secret_id.action_enable()
            # Kick any stale session so the customer reconnects fresh.
            try:
                sub.action_kick_session()
            except Exception:
                pass  # No active session is fine — ignore silently.

        msg_parts = [
            f"Payment promise granted: {days} day(s), deadline {self.promesa_date}."
        ]
        if self.note:
            msg_parts.append(f"Note: {self.note}")

        sub.write({
            "isp_status": "promesa",
            "promesa_deadline": self.promesa_date,
            "promesa_days": days,
        })
        sub.message_post(body="<br/>".join(msg_parts))
        return {"type": "ir.actions.act_window_close"}
