# Temporary packed server: expands full Handler + timeout + fast official parse + T1 fallback.
# Follow-up: replace with readable server.py once deploy is green.
import bz2, base64
_SRC = bz2.decompress(base64.b64decode('PLACEHOLDER'))
exec(compile(_SRC, 'server.py', 'exec'), globals())
