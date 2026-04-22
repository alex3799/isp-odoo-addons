import ipaddress
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _parse_pool_ips(ranges_str):
    """Parse a RouterOS pool ranges string into a list of IPv4Address objects."""
    ips = []
    for part in ranges_str.split(','):
        part = part.strip()
        if not part:
            continue
        if '/' in part:
            net = ipaddress.ip_network(part, strict=False)
            ips.extend(net.hosts())
        elif '-' in part:
            start_s, end_s = part.split('-', 1)
            current = ipaddress.ip_address(start_s.strip())
            end = ipaddress.ip_address(end_s.strip())
            while current <= end:
                ips.append(current)
                current += 1
        else:
            ips.append(ipaddress.ip_address(part))
    return ips


class MikrotikIpPool(models.Model):
    _name = "mikrotik.ip.pool"
    _description = "MikroTik IP Pool"
    _rec_name = "name"
    _order = "name"
    _inherit = ["mail.thread"]

    name = fields.Char(string="Pool Name", required=True, tracking=True)
    ranges = fields.Char(string="Ranges", tracking=True)
    next_pool = fields.Char(string="Next Pool")
    available = fields.Integer(string="Available")
    total = fields.Integer(string="Total")
    used = fields.Integer(string="Used")

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
        help="The .id field from RouterOS (e.g. *2). Used as sync key.",
    )
    last_sync = fields.Datetime(string="Last Synced", readonly=True)
    active = fields.Boolean(default=True)

    # ── IP Address tracking ───────────────────────────────────────────────────

    ip_address_ids = fields.One2many(
        'mikrotik.ip.address',
        'pool_id',
        string='IP Addresses',
    )
    ip_address_count = fields.Integer(
        string='Total IPs',
        compute='_compute_ip_address_counts',
    )
    ip_available_count = fields.Integer(
        string='Available',
        compute='_compute_ip_address_counts',
    )
    ip_assigned_count = fields.Integer(
        string='Assigned',
        compute='_compute_ip_address_counts',
    )

    _sql_constraints = [
        (
            "unique_mikrotik_id_router",
            "UNIQUE(mikrotik_id, router_id)",
            "An IP pool with this RouterOS ID already exists on this router.",
        ),
    ]

    def _compute_ip_address_counts(self):
        IpAddress = self.env['mikrotik.ip.address']
        total_data = IpAddress.read_group(
            [('pool_id', 'in', self.ids)],
            ['pool_id'],
            ['pool_id'],
        )
        avail_data = IpAddress.read_group(
            [('pool_id', 'in', self.ids), ('state', '=', 'available')],
            ['pool_id'],
            ['pool_id'],
        )
        assigned_data = IpAddress.read_group(
            [('pool_id', 'in', self.ids), ('state', '=', 'assigned')],
            ['pool_id'],
            ['pool_id'],
        )
        total_map = {r['pool_id'][0]: r['pool_id_count'] for r in total_data}
        avail_map = {r['pool_id'][0]: r['pool_id_count'] for r in avail_data}
        assigned_map = {r['pool_id'][0]: r['pool_id_count'] for r in assigned_data}
        for pool in self:
            pool.ip_address_count = total_map.get(pool.id, 0)
            pool.ip_available_count = avail_map.get(pool.id, 0)
            pool.ip_assigned_count = assigned_map.get(pool.id, 0)

    def action_generate_addresses(self):
        self.ensure_one()
        if not self.ranges:
            raise UserError(_('No ranges defined for this pool.'))

        existing = set(
            self.env['mikrotik.ip.address']
            .search([('pool_id', '=', self.id)])
            .mapped('name')
        )

        try:
            all_ips = _parse_pool_ips(self.ranges)
        except ValueError as e:
            raise UserError(_('Invalid ranges: %s') % str(e))

        new_vals = []
        for ip in all_ips:
            ip_str = str(ip)
            last_octet = int(ip_str.rsplit('.', 1)[1])
            if last_octet in (0, 255):
                continue
            if ip_str not in existing:
                new_vals.append({'name': ip_str, 'pool_id': self.id})

        if new_vals:
            self.env['mikrotik.ip.address'].create(new_vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Done'),
                'message': _('%d new IP addresses created.') % len(new_vals),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_view_ip_addresses(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('IP Addresses — %s') % self.name,
            'res_model': 'mikrotik.ip.address',
            'view_mode': 'list,form',
            'domain': [('pool_id', '=', self.id)],
            'context': {'default_pool_id': self.id},
        }

    def action_view_available_ips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Available IPs — %s') % self.name,
            'res_model': 'mikrotik.ip.address',
            'view_mode': 'list',
            'domain': [('pool_id', '=', self.id), ('state', '=', 'available')],
        }

    def action_view_assigned_ips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Assigned IPs — %s') % self.name,
            'res_model': 'mikrotik.ip.address',
            'view_mode': 'list',
            'domain': [('pool_id', '=', self.id), ('state', '=', 'assigned')],
        }
