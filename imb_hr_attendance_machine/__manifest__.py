{
    'name': 'Attendance Machine (Solution X401)',
    'version': '10.0.1.0.1',
    'category': 'Human Resources',
    'summary': """
       Attendance Machine (Solution X401)
       """,
    'description': """
        Attendance Machine (Solution X401)

        imbarbudiman@yahoo.com
    """,
    'author': "Imbar Budiman",
    'website': "http://budimansoft.com/",

    'depends': [
        'hr_attendance',
    ],

    'data': [
        'security/ir.model.access.csv',

        'views/hr_view.xml',
        'views/hr_attendance_machine_view.xml',

        'data/hr_attendance_machine_data.xml',
    ],
    
    'installable': True,
}
