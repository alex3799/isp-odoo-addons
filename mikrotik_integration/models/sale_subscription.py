# Copyright 2024 Mundo1Telecom
# License LGPL-3.0 or later
import calendar
import ipaddress
import logging
import secrets
import string
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mikrotik_api import mikrotik_notification, mikrotik_rest_call

_logger = logging.getLogger(__name__)

_ALNUM = string.ascii_lowercase + string.digits


def _parse_pool_ips(ranges_str):
    """Parse a RouterOS pool ranges string into a list of IPv4Address objects.

    Supports:
      - CIDR:              10.200.0.0/19
      - Dash range:        10.50.50.2-10.50.50.254
      - Single IP:         192.168.1.5
      - Comma-separated combinations of the above
    """
    ips = []
    for part in ranges_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            net = ipaddress.ip_network(part, strict=False)
            ips.extend(net.hosts())
        elif "-" in part:
            start_s, end_s = part.split("-", 1)
            start = ipaddress.ip_address(start_s.strip())
            end = ipaddress.ip_address(end_s.strip())
            current = start
            while current <= end:
                ips.append(current)
                current += 1
        else:
            ips.append(ipaddress.ip_address(part))
    return ips


class SaleSubscription(models.Model):
    _inherit = "sale.subscription"

    service_plan_id = fields.Many2one(
        comodel_name="mikrotik.service.plan",
        string="Service Plan",
        tracking=True,
    )
    plan_speed_label = fields.Char(
        related="service_plan_id.speed_label",
        string="Speed",
        readonly=True,
    )
    ppp_secret_id = fields.Many2one(
        comodel_name="mikrotik.ppp.secret",
        string="PPP Secret",
        tracking=True,
        readonly=True,
    )
    isp_status = fields.Selection(
        selection=[
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("promesa", "Payment Promise"),
            ("voluntary_suspend", "Voluntary Suspend"),
        ],
        string="ISP Status",
        default="active",
        tracking=True,
    )
    promesa_deadline = fields.Date(string="Promise Deadline", tracking=True)
    promesa_days = fields.Integer(string="Promise Days")
    effective_cut_date = fields.Date(
        string="Effective Cut Date",
        compute="_compute_effective_cut_date",
        store=True,
    )

    # PPPoE provisioning
    ppp_username = fields.Char(string="PPPoE Username")
    ppp_password = fields.Char(string="PPPoE Password")
    ppp_router_id = fields.Many2one(
        comodel_name="mikrotik.router",
        string="Router",
    )
    ppp_provisioned = fields.Boolean(
        string="Provisioned",
        default=False,
        readonly=True,
    )

    # Static IP assignment
    ppp_ip_pool_id = fields.Many2one(
        comodel_name="mikrotik.ip.pool",
        string="IP Pool",
        help="Select a pool to auto-assign the next available IP, or type an IP manually.",
    )
    ppp_remote_address = fields.Char(
        string="Customer IP Address",
        help="Static IP set as remote-address on the PPP secret.",
    )

    # ── Computed fields ──────────────────────────────────────────────────────

    @api.depends("service_plan_id.cut_day", "promesa_deadline")
    def _compute_effective_cut_date(self):
        for rec in self:
            cut_day = rec.service_plan_id.cut_day if rec.service_plan_id else 0
            if not cut_day:
                rec.effective_cut_date = rec.promesa_deadline or False
                continue
            today = date.today()
            last_day_current = calendar.monthrange(today.year, today.month)[1]
            d = min(cut_day, last_day_current)
            cut_date = today.replace(day=d)
            if cut_date <= today:
                next_month = today + relativedelta(months=1)
                last_day_next = calendar.monthrange(next_month.year, next_month.month)[1]
                d = min(cut_day, last_day_next)
                cut_date = next_month.replace(day=d)
            while cut_date.weekday() >= 5:
                cut_date += relativedelta(days=1)
            if rec.promesa_deadline and rec.promesa_deadline > cut_date:
                cut_date = rec.promesa_deadline
            rec.effective_cut_date = cut_date

    # ── Onchange ─────────────────────────────────────────────────────────────

    @api.onchange("service_plan_id")
    def _onchange_service_plan_id_pppoe(self):
        """Auto-generate PPPoE credentials when service plan is set and fields are empty."""
        if self.service_plan_id and not self.ppp_username and not self.ppp_password:
            self.ppp_username = "user-" + "".join(
                secrets.choice(_ALNUM) for _ in range(8)
            )
            self.ppp_password = secrets.token_urlsafe(12)[:16]

    # ── ISP status actions ────────────────────────────────────────────────────

    def action_isp_suspend(self):
        self.ensure_one()
        if self.ppp_secret_id:
            self.ppp_secret_id.action_disable()
        self.isp_status = "suspended"
        self.message_post(body="Service suspended.")

    def action_isp_unsuspend(self):
        self.ensure_one()
        if self.ppp_secret_id:
            self.ppp_secret_id.action_enable()
        self.write({
            "isp_status": "active",
            "promesa_deadline": False,
            "promesa_days": 0,
        })
        self.message_post(body="Service restored.")

    def action_isp_voluntary_suspend(self):
        self.ensure_one()
        if self.ppp_secret_id:
            self.ppp_secret_id.action_disable()
        self.isp_status = "voluntary_suspend"
        self.message_post(body="Service voluntarily suspended by customer request.")

    def action_grant_promesa(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "mikrotik.promesa.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_subscription_id": self.id},
        }

    def action_kick_session(self):
        self.ensure_one()
        if not self.ppp_secret_id:
            raise UserError("No PPP secret linked to this subscription.")
        secret = self.ppp_secret_id
        router = secret.router_id
        active_sessions = mikrotik_rest_call(router, "GET", "ppp/active", timeout=15)
        if not isinstance(active_sessions, list):
            active_sessions = []
        session = next(
            (s for s in active_sessions if s.get("name") == secret.name), None
        )
        if not session:
            raise UserError(
                "No active PPP session found for "
                + repr(secret.name)
                + " on "
                + router.name
                + "."
            )
        session_id = session.get(".id")
        mikrotik_rest_call(router, "DELETE", f"ppp/active/{session_id}", timeout=15)
        _logger.info("Kicked PPP session for %s (%s)", secret.name, session_id)
        self.message_post(
            body="PPP session for "
            + repr(secret.name)
            + " kicked on "
            + router.name
            + "."
        )

    # ── IP assignment ─────────────────────────────────────────────────────────

    def action_assign_ip_from_pool(self):
        """Find the next available IP in ppp_ip_pool_id and assign it to ppp_remote_address."""
        self.ensure_one()
        if not self.ppp_ip_pool_id:
            raise UserError("Select an IP pool first.")

        pool = self.ppp_ip_pool_id
        if not pool.ranges:
            raise UserError(
                f"IP pool '{pool.name}' has no ranges defined. "
                "Sync it from the router or enter a range manually."
            )

        try:
            all_ips = _parse_pool_ips(pool.ranges)
        except ValueError as exc:
            raise UserError(
                f"Could not parse IP ranges for pool '{pool.name}': {exc}"
            ) from exc

        if not all_ips:
            raise UserError(
                f"No usable IP addresses found in pool '{pool.name}' "
                f"(ranges: {pool.ranges})."
            )

        # Collect assigned IPs from RouterOS (all secrets on this pool's router)
        assigned = set()
        router = pool.router_id
        try:
            routeros_secrets = mikrotik_rest_call(
                router, "GET", "ppp/secret", timeout=20
            )
            if isinstance(routeros_secrets, list):
                for s in routeros_secrets:
                    addr = (s.get("remote-address") or "").strip()
                    if addr:
                        try:
                            assigned.add(ipaddress.ip_address(addr))
                        except ValueError:
                            pass
        except Exception as exc:
            _logger.warning(
                "Could not fetch PPP secrets from router %s for IP conflict check: %s",
                router.name, exc,
            )

        # Also reserve IPs already recorded in Odoo (may not be pushed yet)
        odoo_addresses = self.search([
            ("ppp_remote_address", "!=", False),
            ("id", "!=", self.id),
        ]).mapped("ppp_remote_address")
        for addr_str in odoo_addresses:
            if addr_str:
                try:
                    assigned.add(ipaddress.ip_address(addr_str.strip()))
                except ValueError:
                    pass

        # Find first free IP
        for ip in all_ips:
            if ip not in assigned:
                self.ppp_remote_address = str(ip)
                _logger.info(
                    "Assigned IP %s from pool '%s' to subscription %s",
                    ip, pool.name, self.name,
                )
                return mikrotik_notification(
                    "IP Assigned",
                    f"Assigned {ip} from pool '{pool.name}'.",
                )

        raise UserError(
            f"IP pool '{pool.name}' is exhausted — "
            f"all {len(all_ips)} addresses are already assigned."
        )

    # ── PPPoE provisioning ────────────────────────────────────────────────────

    def action_provision_pppoe(self):
        """Create or update the PPPoE secret on MikroTik and link it to this subscription."""
        self.ensure_one()
        if not self.ppp_username:
            raise UserError("PPPoE username is required.")
        if not self.ppp_password:
            raise UserError("PPPoE password is required.")
        if not self.service_plan_id:
            raise UserError("Select a service plan before provisioning.")

        profile_name = self.service_plan_id.ppp_profile_name

        if self.ppp_secret_id:
            # Re-provision: update existing secret
            secret = self.ppp_secret_id
            if not secret.mikrotik_id:
                raise UserError(
                    f"PPP secret '{secret.name}' has no RouterOS ID — "
                    "it was never pushed to the router. Delete it and reprovision."
                )
            patch_data = {
                "name": self.ppp_username,
                "password": self.ppp_password,
                "profile": profile_name,
            }
            if self.ppp_remote_address:
                patch_data["remote-address"] = self.ppp_remote_address

            mikrotik_rest_call(
                secret.router_id,
                "PATCH",
                f"ppp/secret/{secret.mikrotik_id}",
                data=patch_data,
                timeout=15,
            )
            odoo_vals = {
                "name": self.ppp_username,
                "password": self.ppp_password,
                "profile": profile_name,
            }
            if self.ppp_remote_address:
                odoo_vals["remote_address"] = self.ppp_remote_address
            secret.sudo().write(odoo_vals)
            self.ppp_provisioned = True

            ip_info = (
                f" | IP: {self.ppp_remote_address}" if self.ppp_remote_address else ""
            )
            self.message_post(
                body=(
                    f"<b>PPPoE secret updated</b> on {secret.router_id.name}."
                    f"<br/>Username: {self.ppp_username} | Profile: {profile_name}{ip_info}"
                )
            )
            return mikrotik_notification(
                "PPPoE Updated",
                f"Secret '{self.ppp_username}' updated on {secret.router_id.name}.",
            )

        # First provision
        if not self.ppp_router_id:
            raise UserError("Select a router before the first provisioning.")

        router = self.ppp_router_id
        put_data = {
            "name": self.ppp_username,
            "password": self.ppp_password,
            "profile": profile_name,
            "service": "pppoe",
        }
        if self.ppp_remote_address:
            put_data["remote-address"] = self.ppp_remote_address

        result = mikrotik_rest_call(router, "PUT", "ppp/secret", data=put_data, timeout=15)
        routeros_id = result.get(".id")

        odoo_create_vals = {
            "name": self.ppp_username,
            "password": self.ppp_password,
            "profile": profile_name,
            "service": "pppoe",
            "router_id": router.id,
            "partner_id": self.partner_id.id if self.partner_id else False,
            "mikrotik_id": routeros_id,
        }
        if self.ppp_remote_address:
            odoo_create_vals["remote_address"] = self.ppp_remote_address

        secret = self.env["mikrotik.ppp.secret"].sudo().create(odoo_create_vals)
        self.write({
            "ppp_secret_id": secret.id,
            "ppp_provisioned": True,
        })
        _logger.info(
            "Provisioned PPPoE secret '%s' on %s (RouterOS ID: %s)",
            self.ppp_username, router.name, routeros_id,
        )
        ip_info = f"<br/>IP: {self.ppp_remote_address}" if self.ppp_remote_address else ""
        self.message_post(
            body=(
                f"<b>PPPoE provisioned</b> on {router.name}."
                f"<br/>Username: {self.ppp_username} | Profile: {profile_name}"
                f"{ip_info}"
                f"<br/>RouterOS ID: {routeros_id}"
            )
        )
        return mikrotik_notification(
            "PPPoE Provisioned",
            f"Secret '{self.ppp_username}' created on {router.name}.",
        )
