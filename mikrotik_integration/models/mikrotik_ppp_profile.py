import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MikrotikPppProfile(models.Model):
    _name = "mikrotik.ppp.profile"
    _description = "MikroTik PPP Profile"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(string="Name", required=True, readonly=True)
    local_address = fields.Char(string="Local Address", readonly=True)
    remote_address = fields.Char(string="Remote Address", readonly=True)
    dns_server = fields.Char(string="DNS Server", readonly=True)
    rate_limit = fields.Char(string="Rate Limit", readonly=True)
    only_one = fields.Char(string="Only One", readonly=True)
    address_list = fields.Char(string="Address List", readonly=True)
    bridge = fields.Char(string="Bridge", readonly=True)
    incoming_filter = fields.Char(string="Incoming Filter", readonly=True)
    outgoing_filter = fields.Char(string="Outgoing Filter", readonly=True)
    interface_list = fields.Char(string="Interface List", readonly=True)
    change_tcp_mss = fields.Char(string="Change TCP MSS", readonly=True)
    use_compression = fields.Char(string="Use Compression", readonly=True)
    use_encryption = fields.Char(string="Use Encryption", readonly=True)
    use_mpls = fields.Char(string="Use MPLS", readonly=True)
    use_upnp = fields.Char(string="Use UPnP", readonly=True)
    comment = fields.Text(string="Comment", readonly=True)

    router_id = fields.Many2one(
        comodel_name="mikrotik.router",
        string="Router",
        required=True,
        ondelete="cascade",
        index=True,
    )
    mikrotik_id = fields.Char(
        string="RouterOS ID",
        readonly=True,
        help="The .id field from RouterOS (e.g. *1). Used as sync key.",
    )
    last_sync = fields.Datetime(string="Last Synced", readonly=True)

    local_pool_id = fields.Many2one(
        comodel_name="mikrotik.ip.pool",
        string="Local Address Pool",
        compute="_compute_pool_ids",
        store=True,
    )
    remote_pool_id = fields.Many2one(
        comodel_name="mikrotik.ip.pool",
        string="Remote Address Pool",
        compute="_compute_pool_ids",
        store=True,
    )

    @api.depends("local_address", "remote_address", "router_id")
    def _compute_pool_ids(self):
        Pool = self.env["mikrotik.ip.pool"]
        for rec in self:
            if rec.router_id and rec.local_address:
                match = Pool.search(
                    [("name", "=", rec.local_address), ("router_id", "=", rec.router_id.id)],
                    limit=1,
                )
                rec.local_pool_id = match.id if match else False
            else:
                rec.local_pool_id = False

            if rec.router_id and rec.remote_address:
                match = Pool.search(
                    [("name", "=", rec.remote_address), ("router_id", "=", rec.router_id.id)],
                    limit=1,
                )
                rec.remote_pool_id = match.id if match else False
            else:
                rec.remote_pool_id = False

    _sql_constraints = [
        (
            "unique_mikrotik_id_router",
            "UNIQUE(mikrotik_id, router_id)",
            "A PPP profile with this RouterOS ID already exists on this router.",
        ),
    ]
