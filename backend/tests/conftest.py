import os

# the suite works as AUTH_DISABLED's implicit user; test_accounts.py turns sign-in on itself
os.environ["AUTH_DISABLED"] = "1"
