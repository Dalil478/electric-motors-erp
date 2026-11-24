الكيتريك موتورز (Electric Motors) — Responsive Cloud-Ready Web App (v3)
=======================================================================

One responsive web app for BOTH Desktop PCs and Phones (Android/iPhone),
backed by a single shared SQLite database (`electric_motors_cloud.db`).

HOW TO HOST ONLINE FREE ON PYTHONANYWHERE (5 MINUTES, NO CREDIT CARD)
---------------------------------------------------------------------
1. Go to https://www.pythonanywhere.com and create a free "Beginner" account.
2. Open the "Files" tab, create a new folder named `electric_motors`, and upload:
     - `app.py`
     - `wsgi.py`
     - `requirements.txt`
   Inside `electric_motors`, create two subfolders:
     - `templates` -> upload `index.html` inside it
     - `static`    -> upload `manifest.json` inside it
   (Or upload the ZIP file, open a Bash console, and run `unzip ElectricMotors_Cloud_v3.zip`.)
3. Open the "Web" tab -> click "Add a new web app" -> choose "Manual configuration" -> Python 3.10+.
4. In the "Code" section, click the WSGI configuration file link, replace its contents with:

       import sys
       path = '/home/YOUR_USERNAME/electric_motors'
       if path not in sys.path:
           sys.path.insert(0, path)
       from app import app as application

   (Replace YOUR_USERNAME with your actual PythonAnywhere username, then click Save.)
5. Click the green "Reload" button at the top of the Web tab.
6. Share your live HTTPS link (`https://YOUR_USERNAME.pythonanywhere.com`) on WhatsApp!
   - On PC: opens with the full desktop sidebar & dashboard.
   - On Phones: automatically switches to the mobile bottom navigation bar & WhatsApp receipts.

Default Login Accounts
----------------------
- Admin (Full DB + Stock + Users + Rate): admin / admin123
- Salesman (POS + Installments):          sales1 / 1234
- Finance (Reports + Payments):           finance1 / 1234
