import secrets
import string

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.mikrotik_api import mikrotik_rest_call

_ALNUM = string.ascii_lowercase + string.digits


class CustomerOnboardingWizard(models.TransientModel):
    _name = 'mikrotik.customer.onboarding.wizard'
    _description = 'Customer Onboarding Wizard'

    installation_request_id = fields.Many2one(
        'mikrotik.installation.request',
        string='Installation Request',
        readonly=True,
    )
    step = fields.Selection(
        [
            ('personal_data', 'Personal Data'),
            ('billing', 'Billing'),
            ('services', 'Services'),
        ],
        string='Step',
        default='personal_data',
    )
    step_label = fields.Char(
        string='Step Label',
        compute='_compute_step_label',
    )

    # ── Step 1: Personal Data ─────────────────────────────────────────────────

    partner_name = fields.Char(string='Customer Name', required=True)
    identification_number = fields.Char(string='ID Number')
    street = fields.Char(string='Street')
    street2 = fields.Char(string='Street2')
    city = fields.Char(string='City')
    state_id = fields.Many2one('res.country.state', string='State')
    country_id = fields.Many2one(
        'res.country',
        string='Country',
        default=lambda self: self.env.ref('base.do', raise_if_not_found=False),
    )
    zip = fields.Char(string='ZIP')
    phone = fields.Char(string='Phone')
    mobile = fields.Char(string='Mobile')
    email = fields.Char(string='Email')
    zone = fields.Char(string='Zone/Sector')

    # ── Step 2: Billing ───────────────────────────────────────────────────────

    router_id = fields.Many2one('mikrotik.router', string='Router')
    service_plan_id = fields.Many2one('mikrotik.service.plan', string='Service Plan')
    plan_speed_label = fields.Char(
        related='service_plan_id.speed_label',
        string='Speed',
        readonly=True,
    )
    cut_day = fields.Integer(string='Cut Day', default=10)

    # ── Step 3: Services ──────────────────────────────────────────────────────

    ip_pool_id = fields.Many2one('mikrotik.ip.pool', string='IP Pool')
    ip_address_id = fields.Many2one(
        'mikrotik.ip.address',
        string='IP Address',
    )
    ppp_remote_address = fields.Char(string='Customer IP')
    ppp_username = fields.Char(string='PPPoE Username')
    ppp_password = fields.Char(string='PPPoE Password')

    # ── Computed ──────────────────────────────────────────────────────────────

    @api.depends('step')
    def _compute_step_label(self):
        labels = {
            'personal_data': 'Step 1 of 3: Personal Data',
            'billing': 'Step 2 of 3: Billing',
            'services': 'Step 3 of 3: Services',
        }
        for rec in self:
            rec.step_label = labels.get(rec.step, '')

    # ── Default get ───────────────────────────────────────────────────────────

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')
        if active_id and active_model == 'mikrotik.installation.request':
            request = self.env['mikrotik.installation.request'].browse(active_id)
            res.update({
                'installation_request_id': request.id,
                'partner_name': request.partner_name,
                'identification_number': request.identification_number,
                'phone': request.phone,
                'mobile': request.mobile,
                'email': request.email,
                'zone': request.zone,
                'street': request.street or False,
                'street2': request.street2 or False,
                'city': request.city or False,
                'state_id': request.state_id.id if request.state_id else False,
                'country_id': request.country_id.id if request.country_id else False,
                'zip': request.zip or False,
            })
        return res

    # ── Step 2 onchanges ──────────────────────────────────────────────────────

    @api.onchange('router_id')
    def _onchange_router_id(self):
        self.service_plan_id = False
        self.ip_pool_id = False
        self.ip_address_id = False
        self.ppp_remote_address = False

    @api.onchange('service_plan_id')
    def _onchange_service_plan_id(self):
        if self.service_plan_id and self.service_plan_id.cut_day:
            self.cut_day = self.service_plan_id.cut_day

    # ── Step 3 onchanges ──────────────────────────────────────────────────────

    @api.onchange('ip_pool_id')
    def _onchange_ip_pool_id(self):
        self.ip_address_id = False
        self.ppp_remote_address = False

    @api.onchange('ip_address_id')
    def _onchange_ip_address_id(self):
        self.ppp_remote_address = self.ip_address_id.name if self.ip_address_id else False

    # ── Credential helpers ────────────────────────────────────────────────────

    def _generate_credentials(self):
        self.ppp_username = 'user-' + ''.join(
            secrets.choice(_ALNUM) for _ in range(8)
        )
        self.ppp_password = secrets.token_urlsafe(12)[:16]

    def action_regenerate_credentials(self):
        self.ensure_one()
        self._generate_credentials()
        return self._reload_wizard()

    def action_regenerate_password(self):
        self.ensure_one()
        self.ppp_password = secrets.token_urlsafe(12)[:16]
        return self._reload_wizard()

    # ── Navigation ────────────────────────────────────────────────────────────

    _STEP_ORDER = ['personal_data', 'billing', 'services']

    def action_next_step(self):
        self.ensure_one()
        idx = self._STEP_ORDER.index(self.step)
        if idx < len(self._STEP_ORDER) - 1:
            next_step = self._STEP_ORDER[idx + 1]
            self.step = next_step
            if next_step == 'services' and not self.ppp_username and not self.ppp_password:
                self._generate_credentials()
        return self._reload_wizard()

    def action_prev_step(self):
        self.ensure_one()
        idx = self._STEP_ORDER.index(self.step)
        if idx > 0:
            self.step = self._STEP_ORDER[idx - 1]
        return self._reload_wizard()

    def _reload_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ── Confirm — create partner, subscription, provision ────────────────────

    def action_confirm(self):
        self.ensure_one()
        if not self.service_plan_id:
            raise UserError(_('Please select a service plan.'))
        if not self.router_id:
            raise UserError(_('Please select a router.'))
        if not self.ppp_username or not self.ppp_password:
            raise UserError(_('PPPoE credentials are required.'))
        # Resolve IP from ip_address_id if onchange didn't fire
        if not self.ppp_remote_address and self.ip_address_id:
            self.ppp_remote_address = self.ip_address_id.name

        if not self.ppp_remote_address:
            raise UserError(_(
                'IP address must be assigned before completing onboarding. '
                'Select an IP pool and assign an IP.'
            ))
        if not self.service_plan_id.subscription_template_id:
            raise UserError(_(
                'Service plan "%s" has no subscription template configured. '
                'Please set one on the service plan before continuing.'
            ) % self.service_plan_id.name)

        # ── Fix 12: Duplicate PPPoE username ─────────────────────────────────
        existing_odoo = self.env['mikrotik.ppp.secret'].search([
            ('name', '=', self.ppp_username),
            ('router_id', '=', self.router_id.id),
        ], limit=1)
        if existing_odoo:
            raise UserError(_(
                "PPPoE username '%s' already exists on router %s. "
                "Please regenerate credentials."
            ) % (self.ppp_username, self.router_id.name))

        # RouterOS side — catches secrets not yet synced to Odoo
        try:
            routeros_secrets = mikrotik_rest_call(
                self.router_id, 'GET', 'ppp/secret', timeout=15
            )
            if isinstance(routeros_secrets, list):
                for s in routeros_secrets:
                    if s.get('name') == self.ppp_username:
                        raise UserError(_(
                            "PPPoE username '%s' already exists on router %s "
                            "(found on router but not yet synced to Odoo). "
                            "Please regenerate credentials."
                        ) % (self.ppp_username, self.router_id.name))
        except UserError:
            raise
        except Exception as exc:
            raise UserError(_(
                "Could not verify username uniqueness on router %s: %s"
            ) % (self.router_id.name, exc)) from exc

        # ── Fix 13: Duplicate IP address ──────────────────────────────────────
        if self.ip_address_id:
            ip_rec = self.env['mikrotik.ip.address'].browse(self.ip_address_id.id)
            if ip_rec.state != 'available':
                raise UserError(_(
                    'IP address %s has already been assigned to another customer.'
                ) % self.ppp_remote_address)
        elif self.ppp_remote_address:
            conflict = self.env['sale.subscription'].search([
                ('ppp_remote_address', '=', self.ppp_remote_address),
                ('ppp_router_id', '=', self.router_id.id),
            ], limit=1)
            if conflict:
                raise UserError(_(
                    'IP address %s has already been assigned to another customer.'
                ) % self.ppp_remote_address)

        # 1. Create res.partner
        partner = self.env['res.partner'].create({
            'name': self.partner_name,
            'street': self.street or False,
            'street2': self.street2 or False,
            'city': self.city or False,
            'state_id': self.state_id.id if self.state_id else False,
            'country_id': self.country_id.id if self.country_id else False,
            'zip': self.zip or False,
            'phone': self.phone or False,
            'mobile': self.mobile or False,
            'email': self.email or False,
            'vat': self.identification_number or False,
        })

        # 2. Determine pricelist (required by OCA subscription)
        pricelist = partner.property_product_pricelist
        if not pricelist:
            pricelist = self.env['product.pricelist'].search(
                [('currency_id', '=', self.env.company.currency_id.id)], limit=1
            )
        if not pricelist:
            raise UserError(_(
                'No pricelist found for this company. '
                'Please configure at least one pricelist first.'
            ))

        # 3. Find in-progress stage
        stage = self.env['sale.subscription.stage'].search(
            [('type', '=', 'in_progress')], limit=1
        )

        # 4. Create sale.subscription
        sub_vals = {
            'partner_id': partner.id,
            'template_id': self.service_plan_id.subscription_template_id.id,
            'pricelist_id': pricelist.id,
            'service_plan_id': self.service_plan_id.id,
            'ppp_username': self.ppp_username,
            'ppp_password': self.ppp_password,
            'ppp_router_id': self.router_id.id,
            'ppp_remote_address': self.ppp_remote_address or False,
            'ppp_ip_pool_id': self.ip_pool_id.id if self.ip_pool_id else False,
            'isp_status': 'active',
            'ppp_provisioned': False,
        }
        if stage:
            sub_vals['stage_id'] = stage.id
        subscription = self.env['sale.subscription'].create(sub_vals)

        # 5. Mark IP address as assigned
        if self.ip_address_id:
            self.ip_address_id.write({
                'state': 'assigned',
                'subscription_id': subscription.id,
            })

        # 6. Provision PPPoE on the router
        subscription.action_provision_pppoe()

        # 7. Update installation request
        if self.installation_request_id:
            self.installation_request_id.write({'state': 'installed'})
            self.installation_request_id.partner_id = partner
            self.installation_request_id.message_post(
                body=_(
                    'Customer onboarded: <b>%(name)s</b>, '
                    'Plan: <b>%(plan)s</b>, '
                    'Router: <b>%(router)s</b>, '
                    'IP: <b>%(ip)s</b>'
                ) % {
                    'name': partner.name,
                    'plan': self.service_plan_id.name,
                    'router': self.router_id.name,
                    'ip': self.ppp_remote_address or _('N/A'),
                }
            )

        # 8. Open the new subscription
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.subscription',
            'res_id': subscription.id,
            'view_mode': 'form',
            'target': 'current',
        }
