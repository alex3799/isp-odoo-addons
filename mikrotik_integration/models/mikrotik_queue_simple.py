import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class MikrotikQueueSimple(models.Model):
    _name = "mikrotik.queue.simple"
    _description = "MikroTik Simple Queue"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(string="Name", readonly=True)
    target = fields.Char(string="Target", readonly=True)
    max_limit = fields.Char(string="Max Limit", readonly=True)
    burst_limit = fields.Char(string="Burst Limit", readonly=True)
    burst_threshold = fields.Char(string="Burst Threshold", readonly=True)
    burst_time = fields.Char(string="Burst Time", readonly=True)
    limit_at = fields.Char(string="Limit At", readonly=True)
    priority = fields.Char(string="Priority", readonly=True)
    queue_type = fields.Char(string="Queue Type", readonly=True)
    parent = fields.Char(string="Parent", readonly=True)
    packet_marks = fields.Char(string="Packet Marks", readonly=True)
    disabled = fields.Boolean(string="Disabled", readonly=True, default=False)
    comment = fields.Text(string="Comment", readonly=True)
    dynamic = fields.Boolean(string="Dynamic", readonly=True, default=False)

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

    _sql_constraints = [
        (
            "unique_mikrotik_id_router",
            "UNIQUE(mikrotik_id, router_id)",
            "A simple queue with this RouterOS ID already exists on this router.",
        ),
    ]
