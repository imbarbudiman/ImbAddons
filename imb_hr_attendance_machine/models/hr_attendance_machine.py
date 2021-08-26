import os
import platform
import pytz
import requests
import socket
import logging

from datetime import date, datetime, timedelta
from lxml import etree, html as lxml_html
from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError, ValidationError

_logger = logging.getLogger(__name__)

class HRAttendanceMachine(models.Model):
    """
        - Mesin absen menggunakan cookie 'SessionID' untuk autentifikasi.
        - Field 'cookie' untuk menampung cookie SessionID agar bisa digunakan ulang dan agar tidak menggunakan sesi yang berbeda setiap kali request.
        - Tidak seperti HTTP/Web Server biasa, mesin absen tidak bisa memproduksi banyak cookie,
            jika terlalu banyak request client dengan sesi yang berbeda biasanya tidak akan memberikan cookie lagi untuk beberapa waktu.
    """
    _name = 'hr.attendance.machine'
    _description = 'HR Attendance Machine'
    _order = "port_no asc"

    name = fields.Char(string='Machine IP', required=True, help='Nomor IP dari mesin absen.')
    port_no = fields.Integer(string='Port', required=True, default=80, help='Nomor port.')
    loginid = fields.Char(string='Login ID', required=True, help='Masukan Username atau Login ID.')
    password_or_key = fields.Char(string='Password/Key', required=True, help='Masukan Password/Key/Communication Key.')
    timeout = fields.Integer(string='Timeout', required=True, default=30)
    ip_local = fields.Char(string='IP Mesin Lokal', help='Catatan alamat IP pada jaringan lokal (optional).')
    address_id = fields.Many2one('res.partner', string='Machine Address', help='Lokasi mesin absen ditempatkan.')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.user.company_id.id)
    base_http_url = fields.Char(string='Base HTTP Url', compute='_compute_base_http_url')
    attendance_machine_employee_ids = fields.One2many(
        comodel_name='hr.attendance.machine.employee', inverse_name='attendance_machine_id', string='Attendance Machine Employee')
    attendance_machine_employee_count = fields.Integer(string='Attendance Machine Employee Count', compute='_compute_attendance_machine_employee_count')
    cookie = fields.Text(string='HTTP Cookie', readonly=True)
    active = fields.Boolean(default=True, help='Jika mesin absen Tidak Aktif, tidak akan dipanggil pada saat eksekusi cron "Download Attendance Log", tapi tetap bisa dowload data absen dengan klik tombol "Download Data".')

    @api.depends('name', 'port_no')
    def _compute_base_http_url(self):
        for machine in self:
            machine.base_http_url = 'http://%s:%s' % (machine.name, machine.port_no)

    @api.depends('attendance_machine_employee_ids')
    def _compute_attendance_machine_employee_count(self):
        for r in self:
            r.attendance_machine_employee_count = len(r.attendance_machine_employee_ids)
    
    @api.multi
    @api.constrains('name')
    def _check_validity_name(self):
        for machine in self:
            try:
                socket.inet_aton(machine.name)
            except socket.error:
                raise ValidationError(_('Format Machine IP tidak benar.'))
    
    # ORM Overriding
    ###############

    @api.multi
    def name_get(self):
        result = []
        for s in self:
            result.append((s.id, "%s:%s (%s)" % (s.name, s.port_no, s.ip_local)))
        return result

    # Bussines Method
    ###############

    @api.multi
    def try_connection(self):
        for machine in self:
            machine_ip = machine.name
            try:
                req = requests.head(machine.base_http_url, timeout=machine.timeout)
                # HTTP errors are not raised by default, this statement does that
                req.raise_for_status()
                raise UserError(_('Test koneksi berhasil!'))
            except requests.exceptions.RequestException as e:
                raise UserError(_('Test koneksi gagal: %s' % (e)))
            return False
    
    def _send_request_get(self, endpoints, req_session):
        self.ensure_one()
        if endpoints and req_session:
            try:
                res = req_session.get(self.base_http_url + endpoints, timeout=self.timeout)
                res.raise_for_status()
                return res
            except requests.exceptions.Timeout:
                raise UserError(_('Waktu sambungan telah habis (Timeout).'))
            except requests.exceptions.TooManyRedirects:
                raise UserError(_('Terlalu banyak pengalihan sambungan.'))
            except requests.exceptions.RequestException as e:
                raise UserError(_('Ada masalah dengan sambungan.'))
    
    def _send_request_post(self, endpoints, req_session, data=None, from_cron=False):
        self.ensure_one()
        if endpoints and req_session:
            if not from_cron:
                try:
                    res = req_session.post(self.base_http_url + endpoints, data=data, timeout=self.timeout)
                    res.raise_for_status()
                    return res
                except requests.exceptions.Timeout:
                    raise UserError(_('Waktu sambungan telah habis (Timeout).'))
                except requests.exceptions.TooManyRedirects:
                    raise UserError(_('Terlalu banyak pengalihan sambungan.'))
                except requests.exceptions.RequestException as e:
                    raise UserError(_('Ada masalah dengan sambungan.'))
            else:
                try:
                    res = req_session.post(self.base_http_url + endpoints, data=data, timeout=self.timeout)
                    return res
                except requests.exceptions.Timeout:
                    _logger.warning(self.display_name + ': ' + _('Waktu sambungan telah habis (Timeout).'))
                    return False
                except requests.exceptions.TooManyRedirects:
                    _logger.warning(self.display_name + ': ' + _('Terlalu banyak pengalihan sambungan.'))
                    return False
                except requests.exceptions.RequestException as e:
                    _logger.warning(self.display_name + ': ' + _('Ada masalah dengan sambungan.'))
                    return False

    def _get_cookie_login(self, from_cron=False):
        self.ensure_one()
        timeout = self.timeout
        base_http_url = self.base_http_url
        req_session = requests.Session()

        try:
            if not self.cookie:
                # Kirim request hanya untuk mendapat cookie SessionID
                # req_session akan otomatis mengandung cookies jika header balasan berisi cookie
                res = req_session.get(base_http_url, timeout=timeout)
                if not req_session.cookies:
                    if not from_cron:
                        raise UserError(_('Mesin absen terlalu banyak menerima permintaan dari Sesi yang berbeda. Tunggu beberapa waktu dan coba lagi.'))
                    _logger.warning(self.display_name + ': ' + _('Mesin absen terlalu banyak menerima permintaan dari Sesi yang berbeda. Tunggu beberapa waktu dan coba lagi.'))
                    return False
                
                for c in req_session.cookies:
                    if c.name == 'SessionID':
                        self.cookie = str({
                            'name': c.name,
                            'value': c.value,
                            'domain': c.domain,
                            'path': c.path
                        })
                        break

            # Ambil data SessionID dari DB, lalu set pada objek 'req_session'
            sess_id = eval(self.cookie)
            req_session.cookies.set('SessionID', sess_id.get('value'), domain=sess_id.get('domain'), path=sess_id.get('path'))

            res = req_session.post(base_http_url+'/csl/check', timeout=timeout, data={
                'username': self.loginid,
                'userpwd': self.password_or_key,
            })
 
            # Check struktur HTML ketika login berhasil (Halaman /csl/check), jika sesuai: return req_session
            root = lxml_html.fromstring(res.text)
            if root.xpath('/html/frameset/frameset/frame[@name="menu"]'):
                return req_session
            
            # Jika response tidak berisi HTML yang sesuai struktur halaman /csl/check, bisa jadi karena session expired.
            # Hapus cookie SessionID untuk nanti dapat yang baru ketika request cookie ulang.
            self.cookie = False
            self.env.cr.commit()
            req_session.cookies.clear()

            if not from_cron:
                raise UserError(_('Sesi sambungan bermasalah. Silahkan coba lagi, jika belum berhasil tunggu beberapa waktu sebelum melakukan sambungan lagi.'))
            _logger.warning(self.display_name + ': ' + _('Sesi sambungan bermasalah. Silahkan coba lagi, jika belum berhasil tunggu beberapa waktu sebelum melakukan sambungan lagi.'))            
            
            return False
        except requests.HTTPError as e:
            if not from_cron:
                raise UserError(_('Sambungan http bermasalah, kode status %s.' % (e.response.status_code)))
            _logger.warning(self.display_name + ': ' + _('Sambungan http bermasalah, kode status %s.' % (e.response.status_code)))
            return False
        except requests.ConnectionError:
            if not from_cron:
                raise UserError(_('Sambungan gagal!'))
            _logger.warning(self.display_name + ': ' + _('Sambungan gagal!'))
            return False

    @api.multi
    def sync_employee_id(self):
        machine_employee = self.env['hr.attendance.machine.employee']

        for machine in self:
            req_session = machine._get_cookie_login()
            if not req_session:
                continue

            res = machine._send_request_get('/csl/user', req_session)
            root = lxml_html.fromstring(res.text)

            if not root.xpath('//div[@id="cc"]/table/tr'):
                raise UserError(_('Terjadi kesalahan pada data yang diterima.'))

            list_device_user = []
            for tr in root.xpath('//div[@id="cc"]/table/tr'):
                td = tr.xpath('./td')
                list_device_user.append({
                    'device_pk_employee_id': td[0].xpath('./input[@name="uid"]/@value')[0],
                    'device_employee_id': td[2].text_content(),
                    'name': td[3].text_content(),
                    'card_id': td[4].text_content(),
                })
                
            for empl in self.env['hr.employee'].search([]):
                if empl.device_employee_id:
                    user_dict = next((user for user in list_device_user if user['device_employee_id'] == empl.device_employee_id), None)
                  
                    if user_dict:
                        next_empl_loop = False
                        for machine_employee_id in machine.attendance_machine_employee_ids:
                            # Jika sudah ada di hr.attendance.machine.employee, update field device_pk_employee_id dengan yang terbaru dari mesin absen
                            # Kemudian keluar dari loop 
                            if machine_employee_id.employee_id.device_employee_id == user_dict.get('device_employee_id'):
                                machine_employee_id.device_pk_employee_id = user_dict.get('device_pk_employee_id')
                                # print '===================== update:', machine_employee_id.employee_id.name, user_dict.get('device_pk_employee_id')
                                next_empl_loop = True
                                break
                        
                        if next_empl_loop:
                            continue # Stop dan lanjutkan ke loop employee selanjutnya
                        
                        # Jika tidak ada di hr.attendance.machine.employee, buat record.
                        machine_employee.create({
                            'attendance_machine_id': machine.id,
                            'employee_id': empl.id,
                            'device_pk_employee_id': user_dict.get('device_pk_employee_id'),
                        })
                        # print '=================== buat baru:', empl.name, user_dict.get('device_pk_employee_id')
     
    @api.multi
    def _cron_download_attendance(self):
        machines = self.search([('active', '=', True)])
        machines.download_attendance(from_cron=True)
     
    # Parameter ke 2 (arg) selalu otomatis terisi jika dipanggil dari klik button.
    # Jadi from_cron harus ada di parameter ke 3.
    @api.multi
    def download_attendance(self, arg=None, from_cron=False):
        # Jika di eksekusi dari cron, timezone akun admin yg digunakan (context_timestamp).
        # Method ini harus di eksekusi oleh user yang timezone nya sama dengan mesin absen yaitu 'Asia/Jakarta'.
        today_user_tz = fields.Datetime.context_timestamp(self, datetime.today())
        hour_23 = today_user_tz.replace(hour=23, minute=0, second=0, microsecond=0)
        hour_1 = today_user_tz.replace(hour=1, minute=0, second=0, microsecond=0)

        """
        Karena ada kemungkinan waktu di mesin absen tidak sama dengan server odoo (beberapa detik atau menit).
        Untuk validitas, data absen tidak bisa didownload jika lebih dari jam 23 atau sebelum jam 1.
        Ini untuk mencegah perbedaan hari antara mesin absen dengan server odoo ketika cron di eksekusi sekitar jam pergantian hari (23:59 - 00:01).
        Jika hari berbeda antara server odoo dengan mesin absen akan terbuat/terupdate record hr.attendance yang tidak tepat.
        """
        if today_user_tz > hour_23 or today_user_tz < hour_1:
            _logger.warning('Data absen tidak bisa di tarik jika di atas jam 23 atau sebelum jam 1.')
            raise AccessError(_('Data absen tidak bisa di tarik jika di atas jam 23 atau sebelum jam 1.'))

        employee = self.env['hr.employee']
        user_tz = pytz.timezone(self._context.get('tz') or self.env.user.tz)
        
        for machine in self:
            _logger.info('================ Mesin: ' + machine.display_name + ' ================')
            if not machine.attendance_machine_employee_ids:
                continue
            
            req_session = machine._get_cookie_login(from_cron=from_cron)
            if not req_session:
                continue
            
            uids = machine.attendance_machine_employee_ids.mapped('device_pk_employee_id')
            payload = {
                'sdate': today_user_tz.strftime('%Y-%m-%d'),
                'edate': today_user_tz.strftime('%Y-%m-%d'),
                'period': 1, # 1 = today
                'uid': sorted(uids),
            }
            
            res = machine._send_request_post('/csl/report?action=run', req_session, data=payload, from_cron=from_cron)
            if not res:
                continue

            root = lxml_html.fromstring(res.text)
            table_tr = root.xpath('//html/body/table/tr')
            
            if not table_tr:
                if not from_cron:
                    raise UserError(_('Terjadi kesalahan pada data yang diterima.'))
                _logger.warning(machine.display_name + ': ' + _('Terjadi kesalahan pada data yang diterima.'))
                continue
                
            if len(table_tr) == 1:
                if not from_cron:
                    raise UserError(_('Belum ada data absen hari ini.'))
                _logger.warning(machine.display_name + ': ' + _('Belum ada data absen hari ini.'))
                continue

            for idx, tr in enumerate(table_tr):
                if idx != 0:
                    # Ambil isi dari element <td>
                    td = tr.xpath('./td')
                    date = str(td[0].text_content())
                    device_employee_id = str(td[1].text_content())
                    name = str(td[2].text_content())
                    time1 = str(td[3].text_content()) # Check-in
                    time2 = str(td[4].text_content())
                    time3 = str(td[5].text_content())
                    time4 = str(td[6].text_content())
                    time5 = str(td[7].text_content())
                    time6 = str(td[8].text_content())
                    
                    emp = employee.search([('device_employee_id', '=', device_employee_id)], limit=1)
                    
                    # print '======================= idx', idx
                    # print '======================= date', date
                    # print '======================= device_employee_id', device_employee_id
                    # print '======================= name', name
                    # print '======================= time1', time1
                    # print '======================= time2', time2
                    # print '======================= time3', time3
                    # print '======================= time4', time4
                    # print '======================= time5', time5
                    # print '======================= time6', time6
                    
                    check_in = fields.Datetime.from_string(date + ' ' + time1)
                    check_in = user_tz.localize(check_in, is_dst=False)
                    check_in_utc = check_in.astimezone(pytz.utc)

                    check_out = time6 or time5 or time4 or time3 or time2 or ''
                    if check_out:
                        check_out = fields.Datetime.from_string(date + ' ' + check_out)
                        check_out = user_tz.localize(check_out, is_dst=False)
                        check_out_utc = check_out.astimezone(pytz.utc)
                    
                    try:
                        if emp.last_attendance_id:
                            last_check_in = fields.Datetime.from_string(emp.last_attendance_id.check_in)
                            last_check_in_usertz = fields.Datetime.context_timestamp(machine, last_check_in)
                            
                            last_date_checkin = fields.Datetime.from_string(last_check_in_usertz.strftime('%Y-%m-%d') + ' 00:00:00')
                            date_attendance_log = fields.Datetime.from_string(date + ' 00:00:00')

                            if check_out and last_date_checkin == date_attendance_log:
                                # delta = check_out - last_check_in_usertz
                                # duration_in_s = delta.total_seconds()
                                # hours = divmod(duration_in_s, 3600)

                                # if hours[0] < 4:
                                #     # Untuk menghidari 'raise exeption' pada method constrains hr_attendance_autoclose.hr_attendance._check_validity() yang akan menyebabkan proses/loop penarikan absen behenti
                                #     # karena employee check-out sebelum 4 jam, maka dicek disini, jika kurang dari 4 jam lewati/continue ke tr selanjutnya tanpa write/update.
                                #     _logger.warning(
                                #         machine.display_name + ': Employee ' + emp.name + ' check-out sebelum 4 jam. Waktu check-out tidak disimpan.')
                                #     continue

                                emp.last_attendance_id.write({
                                    'check_out': fields.Datetime.to_string(check_out_utc),
                                })

                                # Simpan/commit manual,
                                # agar jika ada error di loop tr/absen atau mesin berikutnya sudah tersimpan dan tidak ikut di rollback.
                                # machine = self
                                machine.env.cr.commit()

                                # print '======================================= UPDATE checkout:', emp.name, check_out_utc

                            elif check_in_utc and last_date_checkin < date_attendance_log:
                                self.env['hr.attendance'].create({
                                    'employee_id': emp.id,
                                    'check_in': fields.Datetime.to_string(check_in_utc),
                                    'attendance_type': 'wfo',
                                })
                                machine.env.cr.commit()

                                # print '======================================= CREATE checkin:', emp.name, check_in_utc

                        else: 
                            # Jika employee yang belum ada rocord absen samasekali
                            self.env['hr.attendance'].create({
                                'employee_id': emp.id,
                                'check_in': fields.Datetime.to_string(check_in_utc),
                                'attendance_type': 'wfo',
                            })
                            machine.env.cr.commit()

                            # print '======================================= CREATE checkin First:', emp.name, check_in_utc
                        
                    except Exception as e:
                        machine.env.cr.rollback()
                        if not from_cron:
                            raise UserError('Ada kesalahan: ' + emp.name + ': ' + str(e) + '.')
                        _logger.warning(machine.display_name + ': Ada kesalahan: ' + emp.name + ': ' + str(e) + '.')
                                
                    # print '\r\n'
                    

class AttendanceMachineEmployee(models.Model):
    """
        ID yang digunakan untuk mendapat log absen adalah device_pk_employee_id bukan hr_employee.device_employee_id (ID Number pada Web 3.0).
        Ini seperti Primary Key auto increment internal dari mesin absen.
        Bisa dilihat dengan cara inspect elemen di tabel/menu User pada Web 3.0.
    """

    _name = 'hr.attendance.machine.employee'
    _description = 'Attendance Machine Employee'

    attendance_machine_id = fields.Many2one(comodel_name='hr.attendance.machine', string='Attendance Machine')
    employee_id = fields.Many2one(comodel_name='hr.employee', readonly=True, string='Employee')
    device_pk_employee_id = fields.Char(string='Device Employee Primary Key', readonly=True, help='ID PK Employee pada mesin absen.')
    
    @api.multi
    def name_get(self):
        result = []
        for s in self:
            result.append((s.id, "%s (ID Number: %s)" %
                          (s.employee_id.name, s.employee_id.device_employee_id)))
        return result

