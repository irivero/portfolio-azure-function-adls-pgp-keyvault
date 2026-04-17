#!/bin/bash
# Startup script for Azure Linux App Service
# Installs GnuPG 2 required for PGP operations

echo "Starting initialization script..."

# Update package list and install gnupg2
apt-get update
apt-get install -y gnupg2

# Verify installation
if command -v gpg2 &> /dev/null; then
    echo "✓ GnuPG 2 installed successfully"
    gpg2 --version
else
    echo "✗ GnuPG 2 installation failed"
    exit 1
fi

echo "Initialization complete."
