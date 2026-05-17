# Shlink MCP Server — package marker.
#
# The wheel ships these modules at the top level (PYTHONPATH=/app/src in the
# container). Do not import application code from this file - flat absolute
# imports (`from config import Settings`) are the convention.
