"""
PythonAnywhere WSGI entry point.
In PythonAnywhere -> Web -> WSGI configuration file, set:
    import sys
    path = '/home/YOUR_USERNAME/electric_motors'
    if path not in sys.path:
        sys.path.insert(0, path)
    from app import app as application
"""
from app import app as application
