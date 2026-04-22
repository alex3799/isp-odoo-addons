import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MikrotikIpAddress(models.Model):
    _name = 'mikrotik.ip.address'
    _description = 'MikroTik IP Address'
    _rec_name = 'name'
    _order = 'sort_key'

    name = fields.Char(string='IP Address', required=True, index=True)
    pool_id = fields.Many2one(
        'mikrotik.ip.pool',
        string='IP Pool',
        required=True,
        ondelete='cascade',
        index=True,
    )
    router_id = fields.Many2one(
        'mikrotik.router',
        string='Router',
        related='pool_id.router_id',
        store=True,
    )
    subnet = fields.Char(
        string='Subnet',
        compute='_compute_subnet',
        store=True,
    )
    state = fields.Selection(
        [
            ('available', 'Available'),
            ('assigned', 'Assigned'),
            ('reserved', 'Reserved'),
        ],
        string='State',
        default='available',
        required=True,
        index=True,
    )
    subscription_id = fields.Many2one(
        'sale.subscription',
        string='Subscription',
        ondelete='set null',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        related='subscription_id.partner_id',
        store=True,
    )
    sort_key = fields.Char(
        string='Sort Key',
        compute='_compute_sort_key',
        store=True,
    )

    _sql_constraints = [
        (
            'unique_ip_pool',
            'UNIQUE(name, pool_id)',
            'This IP address already exists in this pool.',
        ),
    ]

    @api.depends('name')
    def _compute_subnet(self):
        for rec in self:
            if rec.name:
                parts = rec.name.rsplit('.', 1)
                rec.subnet = parts[0] + '.0' if len(parts) == 2 else False
            else:
                rec.subnet = False

    @api.depends('name')
    def _compute_sort_key(self):
        for rec in self:
            if rec.name:
                try:
                    parts = rec.name.split('.')
                    rec.sort_key = '.'.join(p.zfill(3) for p in parts)
                except Exception:
                    rec.sort_key = rec.name
            else:
                rec.sort_key = False
