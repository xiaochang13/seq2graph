import sys
import datetime

level = 1
file = sys.stderr

def writeln(s=""):
    file.write("%s\n" % s)
    file.flush()

def write(s):
    file.write(s)
    file.flush()
