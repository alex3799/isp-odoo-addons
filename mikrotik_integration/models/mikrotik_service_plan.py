import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mikrotik_api import mikrotik_notification, mikrotik_rest_call

_logger = logging.getLogger(__name__)


class MikrotikServicePlan(models.Model):
    _name = "mikrotik.service.plan"
    _description = "MikroTik Service Plan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, name"

    name = fields.Char(required=True, help="Display name, e.g. Fibra 100 Mbps")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # Speed display
    download_speed = fields.Integer(string="Download (Mbps)", required=True)
    upload_speed = fields.Integer(string="Upload (Mbps)", required=True)
    speed_label = fields.Char(
        string="Speed Label",
        compute="_compute_speed_label",
        store=True,
    )

    # PPP profile name — auto-computed from speeds, overridable by user
    ppp_profile_name_manual = fields.Boolean(
        string="Manual Profile Name",
        default=False,
        help="Set when the user has manually overridden the auto-generated profile name.",
    )
    ppp_profile_name = fields.Char(
        string="PPP Profile Name",
        required=True,
        compute="_compute_ppp_profile_name",
        store=True,
        readonly=False,
        inverse="_inverse_ppp_profile_name",
        help="Exact PPP profile name on RouterOS. Auto-generated from speeds; edit to override.",
    )

    # Router assignment
    router_ids = fields.Many2many(
        "mikrotik.router",
        string="Routers",
        help="Routers this plan will be pushed to.",
    )

    # Billing link
    subscription_template_id = fields.Many2one(
        "sale.subscription.template",
        string="Subscription Template",
        help="OCA subscription template that controls billing frequency and price.",
    )
    monthly_price = fields.Float(
        string="Monthly Price",
        digits=(10, 2),
        help="Display price. Actual billing comes from the subscription template.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    # Cut date
    cut_day = fields.Integer(
        string="Cut Day",
        default=10,
        help="Day of month when unpaid subscriptions get suspended. Skips weekends.",
    )

    # Push status
    profile_push_status = fields.Char(
        string="Last Push Result",
        readonly=True,
    )

    # Burst (Fix 14)
    burst_limit = fields.Char(
        string="Burst limit",
        help="Format: upload/download e.g. 20M/40M. Max speed during burst period.",
    )
    burst_time = fields.Char(
        string="Burst time",
        help="Format: upload/download in seconds e.g. 10/10. Duration of burst window.",
    )
    burst_threshold = fields.Char(
        string="Burst threshold",
        help="Format: upload/download e.g. 8M/16M. Average rate that triggers burst end.",
    )

    # QoS (Fix 15)
    limit_at = fields.Char(
        string="Limit at (CIR)",
        help="Format: upload/download e.g. 5M/10M. Committed information rate — guaranteed minimum bandwidth.",
    )
    priority = fields.Selection(
        selection=[
            ("1", "1 - Highest"),
            ("2", "2"),
            ("3", "3"),
            ("4", "4"),
            ("5", "5"),
            ("6", "6"),
            ("7", "7"),
            ("8", "8 - Lowest"),
        ],
        string="Priority",
        default="8",
        help="Queue priority. Lower number = higher priority.",
    )
    contention_ratio = fields.Integer(
        string="Contention ratio",
        default=1,
        help="Number of customers sharing the bandwidth. 1 = dedicated, 8 = 8:1 oversubscription.",
    )

    # Counters
    subscription_count = fields.Integer(
        compute="_compute_subscription_count",
        string="Subscriptions",
    )

    # ── Computed fields ──────────────────────────────────────────────────────

    @api.depends("download_speed", "upload_speed", "ppp_profile_name_manual")
    def _compute_ppp_profile_name(self):
        for rec in self:
            if not rec.ppp_profile_name_manual:
                if rec.download_speed and rec.upload_speed:
                    rec.ppp_profile_name = (
                        f"Plan_{rec.download_speed}M_{rec.upload_speed}M"
                    )

    def _inverse_ppp_profile_name(self):
        """Track whether the user manually overrode the auto-generated name."""
        for rec in self:
            auto = (
                f"Plan_{rec.download_speed}M_{rec.upload_speed}M"
                if rec.download_speed and rec.upload_speed
                else ""
            )
            rec.ppp_profile_name_manual = bool(rec.ppp_profile_name) and (
                rec.ppp_profile_name != auto
            )

    @api.depends("download_speed", "upload_speed")
    def _compute_speed_label(self):
        for rec in self:
            if rec.download_speed and rec.upload_speed:
                rec.speed_label = f"{rec.download_speed} / {rec.upload_speed} Mbps"
            else:
                rec.speed_label = False

    def _compute_subscription_count(self):
        for rec in self:
            rec.subscription_count = self.env["sale.subscription"].search_count(
                [("service_plan_id", "=", rec.id)]
            )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_select_all_routers(self):
        """Set router_ids to all configured routers."""
        self.ensure_one()
        self.router_ids = self.env["mikrotik.router"].search([])

    def action_view_subscriptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Subscriptions — {self.name}",
            "res_model": "sale.subscription",
            "view_mode": "list,form",
            "domain": [("service_plan_id", "=", self.id)],
            "context": {"default_service_plan_id": self.id},
        }

    def action_push_to_routers(self):
        """Create or update the PPP profile on all selected routers.

        RouterOS rate-limit format: rx/tx (router perspective)
          rx = data received by router = client upload
          tx = data sent by router    = client download
        So rate-limit = "{upload}M/{download}M"
        """
        self.ensure_one()
        if not self.download_speed or not self.upload_speed:
            raise UserError("Set both download and upload speeds before pushing.")
        if not self.ppp_profile_name:
            raise UserError("PPP profile name is required.")
        if not self.router_ids:
            raise UserError(
                "No routers selected. Add at least one router or use 'Select All Routers'."
            )

        rate_limit = f"{self.upload_speed}M/{self.download_speed}M"

        # Build profile payload — only include optional fields when set (Fix 16)
        profile_data = {"rate-limit": rate_limit}
        if self.burst_limit:
            profile_data["burst-limit"] = self.burst_limit
        if self.burst_time:
            profile_data["burst-time"] = self.burst_time
        if self.burst_threshold:
            profile_data["burst-threshold"] = self.burst_threshold
        if self.limit_at:
            profile_data["limit-at"] = self.limit_at
        if self.priority:
            profile_data["priority"] = self.priority

        created, updated, errors = [], [], []

        for router in self.router_ids:
            try:
                profiles = mikrotik_rest_call(router, "GET", "ppp/profile", timeout=15)
                existing = next(
                    (p for p in profiles if p.get("name") == self.ppp_profile_name),
                    None,
                )
                if existing:
                    mikrotik_rest_call(
                        router,
                        "PATCH",
                        f"ppp/profile/{existing['.id']}",
                        data=profile_data,
                        timeout=15,
                    )
                    updated.append(router.name)
                    _logger.info(
                        "Updated PPP profile '%s' on %s (rate-limit: %s)",
                        self.ppp_profile_name, router.name, rate_limit,
                    )
                else:
                    create_data = {"name": self.ppp_profile_name}
                    create_data.update(profile_data)
                    mikrotik_rest_call(
                        router,
                        "PUT",
                        "ppp/profile",
                        data=create_data,
                        timeout=15,
                    )
                    created.append(router.name)
                    _logger.info(
                        "Created PPP profile '%s' on %s (rate-limit: %s)",
                        self.ppp_profile_name, router.name, rate_limit,
                    )
            except Exception as exc:
                errors.append(f"{router.name}: {exc}")
                _logger.exception(
                    "Failed to push PPP profile '%s' to %s",
                    self.ppp_profile_name, router.name,
                )

        parts = []
        if created:
            parts.append(f"Created on: {', '.join(created)}")
        if updated:
            parts.append(f"Updated on: {', '.join(updated)}")
        if errors:
            parts.append(f"Errors — {'; '.join(errors)}")

        status = " | ".join(parts) if parts else "No routers processed"
        self.profile_push_status = status
        self.message_post(
            body=(
                f"<b>PPP profile push</b> — <code>{self.ppp_profile_name}</code>"
                f" (rate-limit: {rate_limit})<br/>{status}"
            )
        )

        return mikrotik_notification(
            "Profile Push",
            status,
            ntype="warning" if errors else "success",
        )
