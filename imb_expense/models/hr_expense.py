from odoo import api, fields, models, _


class HrExpense(models.Model):
    _inherit = 'hr.expense'
    
    def create_expense_from_attachments(self, attachment_ids=None, view_type='tree'):
        """ Override method yg digunakan ketika membuat hr.expense dari upload file """
        
        res = super(HrExpense, self).create_expense_from_attachments(attachment_ids=attachment_ids, view_type=view_type)
        attachments = self.env['ir.attachment'].browse(attachment_ids)
        expense_obj = self.env['hr.expense']
        
        for attachment in attachments:
            expense = expense_obj.browse(attachment.res_id)
            expense.message_post(attachment_ids=[attachment.id])
            
        return res
