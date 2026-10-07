"""Tests never touch the real learning data or look up where you are: unless a test sets its own, learned preferences
and interaction records go to a throwaway file, and location detection is off."""

import os
import tempfile

os.environ.setdefault("LEARNING_DB", os.path.join(tempfile.mkdtemp(), "learning.db"))
os.environ.setdefault("LOCATION_SOURCE", "off")
