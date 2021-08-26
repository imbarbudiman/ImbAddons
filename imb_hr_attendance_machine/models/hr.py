from odoo import api, fields, models, _


class Employee(models.Model):

    _inherit = "hr.employee"

    device_employee_id = fields.Char(string='Biometric Device Employee ID', help='ID Employee pada mesin absen (ID Number pada Web 3.0).')

    _sql_constraints = [
        ('device_employee_id_uniq', 'UNIQUE(device_employee_id)', _('Biometric Device Employee ID already exists.'))
    ]
    