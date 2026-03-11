"""PyTest configuration."""

import os

# Set test environment
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "true"
os.environ["DATABASE_ECHO"] = "false"
