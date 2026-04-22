import logging

from odoo import fields, models
from odoo.exceptions import UserError

from .mikrotik_api import mikrotik_notification, mikrotik_rest_call

_logger = logging.getLogger(__name__)


class MikrotikPppSecret(models.Model):
    _name = "mikrotik.ppp.secret"
    _description = "MikroTik PPP Secret (PPPoE credential)"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(string="Username", required=True)
    password = fields.Char(string="Password")
    service = fields.Char(string="Service")
    profile = fields.Char(string="Profile")
    profile_id = fields.Many2one(
        comodel_name="mikrotik.ppp.profile",
        string="PPP Profile",
        ondelete="set null",
        index=True,
    )
    remote_address = fields.Char(string="Remote Address")
    caller_id = fields.Char(string="Caller ID")
    comment = fields.Text(string="Comment")
    disabled = fields.Boolean(string="Disabled", default=False)

    router_id = fields.Many2one(
        comodel_name="mikrotik.router",
        string="Router",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Customer",
        ondelete="set null",
        index=True,
    )
    mikrotik_id = fields.Char(
        string="RouterOS ID",
        readonly=True,
        help="The .id field from RouterOS (e.g. *1A). Used as sync key.",
    )
    last_sync = fields.Datetime(string="Last Synced", readonly=True)

    _sql_constraints = [
        (
            "unique_mikrotik_id_router",
            "UNIQUE(mikrotik_id, router_id)",
            "A PPP secret with this RouterOS ID already exists on this router.",
        ),
    ]

    def _require_mikrotik_id(self):
        self.ensure_one()
        if not self.mikrotik_id:
            raise UserError("This secret has not been synced to a router yet.")

    def _to_routeros_vals(self):
        """Build a RouterOS-compatible dict from this record's fields.

        Only includes fields that have a non-empty value so we don't send
        blank strings to the router (e.g. remote-address: "" causes errors).
        disabled is always included since False is meaningful.
        """
        self.ensure_one()
        vals = {
            "name": self.name,
            "disabled": "yes" if self.disabled else "no",
        }
        if self.password:
            vals["password"] = self.password
        if self.service:
            vals["service"] = self.service
        if self.profile:
            vals["profile"] = self.profile
        if self.remote_address:
            vals["remote-address"] = self.remote_address
        if self.caller_id:
            vals["caller-id"] = self.caller_id
        if self.comment:
            vals["comment"] = self.comment
        return vals

    def _link_profile(self):
        """Try to match profile_id from the profile Char field."""
        Profile = self.env["mikrotik.ppp.profile"]
        for rec in self:
            if rec.profile and rec.router_id:
                match = Profile.search(
                    [("name", "=", rec.profile), ("router_id", "=", rec.router_id.id)],
                    limit=1,
                )
                if match and rec.profile_id != match:
                    rec.sudo().write({"profile_id": match.id})

    def action_push_to_router(self):
        """Push this secret's current values to the router via REST.

        - No mikrotik_id -> PUT (create) and store the returned .id
        - Has mikrotik_id -> PATCH (update) the existing entry
        Returns a success notification.
        """
        self.ensure_one()
        router = self.router_id
        payload = self._to_routeros_vals()

        if not self.mikrotik_id:
            result = mikrotik_rest_call(router, "PUT", "ppp/secret", data=payload, timeout=15)
            new_id = result.get(".id")
            if not new_id:
                raise UserError(
                    f"Router did not return an ID after creating secret '{self.name}'. "
                    f"Response: {result!r}"
                )
            self.sudo().write({"mikrotik_id": new_id, "last_sync": fields.Datetime.now()})
            _logger.info("MikroTik PPP secret created: %s -> %s", self.name, new_id)
            return mikrotik_notification(
                "Secret Created",
                f"PPPoE secret '{self.name}' created on {router.name} (ID: {new_id})",
            )
        else:
            mikrotik_rest_call(
                router, "PATCH", f"ppp/secret/{self.mikrotik_id}", data=payload, timeout=15
            )
            self.sudo().write({"last_sync": fields.Datetime.now()})
            _logger.info("MikroTik PPP secret updated: %s (%s)", self.name, self.mikrotik_id)
            return mikrotik_notification(
                "Secret Updated",
                f"PPPoE secret '{self.name}' updated on {router.name}",
            )

    def action_enable(self):
        """Enable this secret on the router and in Odoo."""
        self._require_mikrotik_id()
        mikrotik_rest_call(
            self.router_id, "PATCH", f"ppp/secret/{self.mikrotik_id}",
            data={"disabled": "no"}, timeout=15,
        )
        self.sudo().write({"disabled": False, "last_sync": fields.Datetime.now()})
        _logger.info("MikroTik PPP secret enabled: %s (%s)", self.name, self.mikrotik_id)
        return mikrotik_notification(
            "Secret Enabled",
            f"PPPoE secret '{self.name}' enabled on {self.router_id.name}",
        )

    def action_disable(self):
        """Disable this secret on the router and in Odoo."""
        self._require_mikrotik_id()
        mikrotik_rest_call(
            self.router_id, "PATCH", f"ppp/secret/{self.mikrotik_id}",
            data={"disabled": "yes"}, timeout=15,
        )
        self.sudo().write({"disabled": True, "last_sync": fields.Datetime.now()})
        _logger.info("MikroTik PPP secret disabled: %s (%s)", self.name, self.mikrotik_id)
        return mikrotik_notification(
            "Secret Disabled",
            f"PPPoE secret '{self.name}' disabled on {self.router_id.name}",
            ntype="warning",
        )

    def action_resync(self):
        """Re-read this single secret from the router and refresh all Odoo fields."""
        self._require_mikrotik_id()
        item = mikrotik_rest_call(
            self.router_id, "GET", f"ppp/secret/{self.mikrotik_id}", timeout=15
        )
        self.sudo().write({
            "name": item.get("name", self.name),
            "password": item.get("password", ""),
            "service": item.get("service", ""),
            "profile": item.get("profile", ""),
            "remote_address": item.get("remote-address", ""),
            "caller_id": item.get("caller-id", ""),
            "comment": item.get("comment", ""),
            "disabled": item.get("disabled", "false").lower() == "true",
            "last_sync": fields.Datetime.now(),
        })
        self._link_profile()
        _logger.info("MikroTik PPP secret resynced: %s (%s)", self.name, self.mikrotik_id)
        return mikrotik_notification(
            "Secret Resynced",
            f"PPPoE secret '{self.name}' refreshed from {self.router_id.name}",
        )

    def action_delete_from_router(self):
        """Delete this secret from the router, then remove the Odoo record."""
        self.ensure_one()
        if not self.mikrotik_id:
            raise UserError(
                f"Secret '{self.name}' has no RouterOS ID -- "
                f"it was never pushed to the router. Delete the Odoo record manually."
            )
        router = self.router_id
        name = self.name
        mikrotik_rest_call(router, "DELETE", f"ppp/secret/{self.mikrotik_id}", timeout=15)
        _logger.info(
            "MikroTik PPP secret deleted from router: %s (%s)", name, self.mikrotik_id
        )
        self.unlink()
        return mikrotik_notification(
            "Secret Deleted",
            f"PPPoE secret '{name}' deleted from {router.name} and removed from Odoo",
        )
