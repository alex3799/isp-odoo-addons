from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MikrotikInstallationRequest(models.Model):
    _name = 'mikrotik.installation.request'
    _description = 'Installation Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_date desc, id desc'

    name = fields.Char(
        string='Reference',
        required=True,
        default=lambda self: self.env['ir.sequence'].next_by_code(
            'mikrotik.installation.request'
        ),
    )
    partner_name = fields.Char(string='Customer Name', required=True)
    partner_id = fields.Many2one('res.partner', string='Existing Customer')
    identification_number = fields.Char(string='ID Number')
    address = fields.Text(string='Installation Address')  # kept for backwards compatibility

    # Structured address (Fix A)
    street = fields.Char(string='Street')
    street2 = fields.Char(string='Sector / Barrio')
    city = fields.Char(string='Municipio')
    state_id = fields.Many2one(
        'res.country.state',
        string='Provincia',
        domain="[('country_id.code', '=', 'DO')]",
    )
    country_id = fields.Many2one(
        'res.country',
        string='Country',
        default=lambda self: self.env.ref('base.do', raise_if_not_found=False),
    )
    zip = fields.Char(string='Zip')
    phone = fields.Char(string='Phone')
    mobile = fields.Char(string='Mobile')
    email = fields.Char(string='Email')
    zone = fields.Char(string='Zone/Sector')
    technician_id = fields.Many2one('res.users', string='Assigned Technician')
    scheduled_date = fields.Date(string='Scheduled Date')
    installation_date = fields.Date(string='Installation Date', readonly=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('scheduled', 'Scheduled'),
            ('installed', 'Installed'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='draft',
        tracking=True,
    )
    notes = fields.Text(string='Notes')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
    )

    def action_schedule(self):
        for rec in self:
            if not rec.technician_id:
                raise ValidationError(
                    _('Please assign a technician before scheduling.')
                )
            if not rec.scheduled_date:
                raise ValidationError(
                    _('Please set a scheduled date before scheduling.')
                )
            rec.state = 'scheduled'

    def action_confirm_installation(self):
        for rec in self:
            rec.write({
                'state': 'installed',
                'installation_date': date.today(),
            })

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'

    def action_open_onboarding(self):
        self.ensure_one()
        if self.partner_id:
            raise UserError(_(
                'This installation already has a customer linked. '
                'Onboarding cannot be run again.'
            ))
        wizard = self.env['mikrotik.customer.onboarding.wizard'].create({
            'installation_request_id': self.id,
            'partner_name': self.partner_name,
            'identification_number': self.identification_number or False,
            'phone': self.phone or False,
            'mobile': self.mobile or False,
            'email': self.email or False,
            'zone': self.zone or False,
            'street': self.street or False,
            'street2': self.street2 or False,
            'city': self.city or False,
            'state_id': self.state_id.id if self.state_id else False,
            'country_id': self.country_id.id if self.country_id else False,
            'zip': self.zip or False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mikrotik.customer.onboarding.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }
