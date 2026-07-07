import quantcontext.server as s, os
print('MODULE FILE:', s.__file__)
print('DIR:', os.path.dirname(s.__file__))
base = os.path.dirname(s.__file__)
import subprocess
out = subprocess.run(['find', base, '-name', '*.py'], capture_output=True, text=True)
print(out.stdout)
