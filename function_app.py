"""
Azure Functions App - Secure File Processing with PGP Encryption
Main application entry point with blueprint registration.
"""
import azure.functions as func

from blueprints.decrypt_kv import bp as decrypt_kv_bp
from blueprints.encrypt import bp as encrypt_bp

app = func.FunctionApp()

# Register function blueprints
app.register_blueprint(decrypt_kv_bp)
app.register_blueprint(encrypt_bp)
