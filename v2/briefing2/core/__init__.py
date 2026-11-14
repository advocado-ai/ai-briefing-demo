"""The functional core: pure functions over frozen data. No network, files, clock or database here.

Rule (the module map, not published here, section 1): a core module may import another core module and
libraries that do no input or output, and never a shell module. tools/check_source_rules.py enforces it.
"""
