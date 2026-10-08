"""Tests never touch the real learning data or look up where you are: unless a test sets its own, learned preferences,
interaction records, goal experiences and turn metrics go to throwaway files, and location detection is off."""

import os
import tempfile

os.environ.setdefault("LEARNING_DB", os.path.join(tempfile.mkdtemp(), "learning.db"))
os.environ.setdefault("EXPERIENCE_DB", os.path.join(tempfile.mkdtemp(), "experience.db"))
os.environ.setdefault("LOCATION_SOURCE", "off")
