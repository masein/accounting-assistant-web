"""سامانه مودیان (Iran's e-invoicing system), roadmap §3.1.

Phase 1 is an EXPORT: invoices are built in the tax organisation's JSON
structure (header / body / payments with the official field codes) and
handed to a trusted provider (شرکت معتمد) or uploaded by the user. Direct
API submission (signing with the company's private key) is phase 2.

* ``taxid``    the 22-character شماره منحصربه‌فرد مالیاتی + Verhoeff check digit
* ``settings`` per-company memory id, default goods/service id, unit, deadline
* ``builder``  one invoice → packet + the problems that block sending it
"""
